# encoding:utf-8
import re
import plugins
from bridge.bridge import Bridge
from bridge.context import ContextType, Context
from bridge.context import ContextType
from bridge.reply import Reply, ReplyType
from channel.chat_message import ChatMessage
from common.log import logger
from plugins import *
import sqlite3
from datetime import datetime


QL_PROMPT = '''
我给你一份json格式的群聊内容：群聊结构如下：
user是发言者，content是发言内容,time是发言时间：
[{'user': '秋风', 'content': '总结',time:'2025-02-26 09:50:53'},{'user': '秋风', 'content': '你好',time:'2025-02-26 09:50:53'},{'user': '小王', 'content': '你好',time:'2025-02-26 09:50:53'}]
-------分割线-------
请用风格简洁干练又不失幽默的语言对我给出的群聊内容总结成一个今日的群聊报告，包含不多于5个话题的总结（如果还有更多话题，可以在后面简单补充,如果话题不足，有几个写几个就可以了，没有必要凑数）。按照热度数量进行降序排列，请用简单的文字回答，不要使用Markdown。你只负责总结群聊内容，不回答任何问题。不要虚构聊天记录，也不要总结不存在的信息。
每个话题包含以下内容：
- 话题名(50字以内，前面带序号1️⃣2️⃣3️⃣）
- 热度(用🔥的数量表示)
- 参与者(不超过5个人，将重复的人名去重)
- 时间段(从几点到几点)
- 过程(50-200字左右）
- 评价(50字以下)
- 分割线： ------------
'''


@plugins.register(
    name="group_chat_summary",
    desire_priority=89,
    hidden=True,
    desc="总结聊天",
    version="0.1",
    author="Other",
)


class GroupChatSummary(Plugin):

    open_ai_api_base = ""
    open_ai_api_key = ""
    open_ai_model = "gpt-4-0613"
    max_record_quantity = 1000
    black_chat_name=[]
    def __init__(self):

        super().__init__()
        try:
            # 加载文件路径
            self.db_path = "./plugins/group_chat_summary/chat_records.db"
            # 连接到SQLite数据库
            try:
                self._connect()
                self._initialize_database()
                logger.debug(f"玩家数据库连接成功！")
            except sqlite3.Error as e:
                logger.error(f"玩家数据库连接或初始化失败: {e}")
                raise

            logger.info("[group_chat_summary] inited")
            self.handlers[Event.ON_HANDLE_CONTEXT] = self.on_handle_context
            self.handlers[Event.ON_RECEIVE_MESSAGE] = self.on_receive_message
        except Exception as e:
            logger.error(f"[group_chat_summary]初始化异常：{e}")
            raise "[group_chat_summary] init failed, ignore "

    def _connect(self) -> None:
        """
        初始化连接（通过 _get_connection 实现）。
        """
        self._get_connection()

    def _initialize_database(self) -> None:
        """
        创建 聊天记录 表和必要的索引，如果它们尚不存在。
        """
        create_table_query = """
        CREATE TABLE IF NOT EXISTS chat_records (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            group_id TEXT,
            user_nickname TEXT,
            content TEXT,
            create_time TEXT,
            UNIQUE(group_id, user_nickname, content, create_time)
        )
        """
        try:
            conn = self._get_connection()
            with conn:
                conn.execute(create_table_query)
            logger.debug("成功初始化数据库表和索引。")
        except sqlite3.Error as e:
            logger.error(f"初始化数据库表或索引失败: {e}")
            raise

    def _get_connection(self) -> sqlite3.Connection:
        """
        获取数据库连接，如果连接不存在则创建。
        """
        if not hasattr(self, 'conn') or self.conn is None:
            try:
                self.conn = sqlite3.connect(self.db_path, check_same_thread=False)
                self.conn.row_factory = sqlite3.Row
                logger.debug("数据库连接已创建并保持打开状态。")
            except sqlite3.Error as e:
                logger.error(f"创建数据库连接失败: {e}")
                raise
        return self.conn

    def on_handle_context(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT
        ]:
            return
        msg: ChatMessage = e_context["context"]["msg"]

        content = e_context["context"].content.strip()
        if content.startswith("总结聊天"):
            reply = Reply()
            reply.type = ReplyType.TEXT
            if msg.other_user_nickname in self.black_chat_name:
                reply.content = "😾我不知道捏~"
                e_context["reply"] = reply
                e_context.action = EventAction.BREAK_PASS
                return
            number = content[4:].strip()
            number_int=99
            if number.isdigit():
                # 转换为整数
                number_int = int(number)
            if e_context["context"]["isgroup"]:
                try:
                    # 从数据库获取聊天记录
                    conn = self._get_connection()
                    with conn:
                        cursor = conn.cursor()
                        cursor.execute('''
                            SELECT user_nickname, content, create_time
                            FROM chat_records
                            WHERE group_id = ?
                            ORDER BY create_time DESC
                            LIMIT ?
                        ''', (msg.other_user_id, number_int))
                        chat_texts = []
                        records = cursor.fetchall()
                        for record in records:
                            nickname = record['user_nickname']
                            content = record['content']
                            create_time = record['create_time']
                            chat_texts.append(f"{create_time} {nickname}: {content}")
                        chat_string = "\n".join(chat_texts)

                        prompt = QL_PROMPT + "----聊天记录如下：\n" + chat_string
                        session_id = e_context["context"]["session_id"]
                        content_dict = {
                            "session_id": session_id,
                        }
                        # 请求大模型
                        context = Context(ContextType.TEXT, prompt, content_dict)
                        reply : Reply = Bridge().fetch_reply_content(prompt, context)
                except Exception as e:
                    logger.error(f"[group_chat_summary]获取聊天记录异常：{e}")
                    reply.content = "😾获取聊天记录失败力~"
            else:
                    reply.content = "🐱小猫咪只为只群聊做总结哦~"
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑

    def process_content(self, content):
        """
        处理各种类型的微信消息内容
        根据消息类型提取有用信息并格式化输出
        """
        # 如果内容不是XML格式，直接返回原内容
        if not isinstance(content, str):
            return "" if content is None else str(content)

        if not (content.strip().startswith('<?xml') or content.strip().startswith('<msg')):
            return content

        # 提取消息类型
        type_match = re.search(r'<type>(\d+)</type>', content)
        msg_type = type_match.group(1) if type_match else "0"

        try:
            # 根据消息类型处理
            if msg_type == "19":  # 聊天记录
                return self.process_chat_history(content)
            elif msg_type == "6":  # 文件
                return self.process_file(content)
            elif msg_type == "4":  # 链接
                return self.process_link(content)
            elif msg_type == "57":  # 引用消息
                return self.process_quote(content)
            elif msg_type == "3":  # 图片
                return self.process_image(content)
            elif msg_type == "1":  # 文本？
                return self.process_text(content)
            elif msg_type == "5":  # 卡片类消息
                return self.process_card(content)
            elif msg_type == "33":  # 小程序
                return self.process_mini_program(content)
            elif msg_type == "2000":  # 转账
                return self.process_transfer(content)
            elif msg_type == "2001":  # 红包
                return self.process_red_packet(content)
            elif msg_type == "47":  # 表情
                return self.process_emoji(content)
            elif msg_type == "49" or msg_type == "42":  # 其他应用消息
                return self.process_app_msg(content)
            else:
                # 通用处理方法
                return self.process_generic(content)
        except Exception as e:
            logger.error(f"处理类型 {msg_type} 的消息时出错: {e}")
            # 出错后尝试通用处理方法
            try:
                return self.process_generic(content)
            except:
                return content  # 如果还是失败，返回原始内容

    def process_chat_history(self, content):
        """处理聊天记录"""
        # 提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else "聊天记录"

        # 提取聊天内容
        des_match = re.search(r'<des>(.*?)</des>', content, re.DOTALL)
        des = des_match.group(1).strip() if des_match else ""

        return f"{title}：\n{des}"

    def process_file(self, content):
        """处理文件消息"""
        # 提取文件名
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else "未知文件"

        # 提取文件大小（如果有）
        size_match = re.search(r'<totallen>(\d+)</totallen>', content)
        size = size_match.group(1) if size_match else ""

        # 提取文件类型（如果有）
        type_match = re.search(r'<fileext>(.*?)</fileext>', content)
        file_type = type_match.group(1) if type_match else ""

        result = f"[文件] {title}"
        if file_type:
            result += f"({file_type})"
        if size:
            try:
                size_int = int(size)
                if size_int > 1024*1024:
                    result += f" {size_int/(1024*1024):.2f}MB"
                elif size_int > 1024:
                    result += f" {size_int/1024:.2f}KB"
                else:
                    result += f" {size_int}B"
            except:
                pass

        return result

    def process_link(self, content):
        """处理链接消息"""
        # 提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else "未知标题"

        # 提取url
        url_match = re.search(r'<url>(.*?)</url>', content)
        url = url_match.group(1) if url_match else ""

        # 提取描述（如果有）
        des_match = re.search(r'<des>(.*?)</des>', content, re.DOTALL)
        des = des_match.group(1).strip() if des_match and des_match.group(1).strip() else ""

        result = f"[标题]:{title}"
        if url:
            result += f"\n[链接]:{url}"
        if des:
            result += f"\n[描述]:{des}"

        return result

    def process_quote(self, content):
        """处理引用消息"""
        # 提取标题（被引用的内容）
        title_match = re.search(r'<title>(.*?)</title>', content)
        if title_match:
            return f"引用: {title_match.group(1)}"

        # 如果没有提取到标题，尝试提取其他信息
        content_match = re.search(r'<content>(.*?)</content>', content, re.DOTALL)
        if content_match:
            return f"引用: {content_match.group(1)}"

        return "引用消息"

    def process_image(self, content):
        """处理图片消息"""
        return "[图片]"

    def process_text(self, content):
        """处理普通文本消息（XML格式的）"""
        content_match = re.search(r'<content>(.*?)</content>', content, re.DOTALL)
        if content_match:
            return content_match.group(1)
        return content

    def process_card(self, content):
        """处理卡片类消息"""
        # 提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else "卡片消息"

        # 提取描述
        des_match = re.search(r'<des>(.*?)</des>', content, re.DOTALL)
        des = des_match.group(1).strip() if des_match else ""

        result = f"[卡片] {title}"
        if des:
            result += f"\n{des}"

        return result

    def process_mini_program(self, content):
        """处理小程序消息"""
        # 提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else ""

        # 提取小程序名称
        displayname_match = re.search(r'<brandname>(.*?)</brandname>', content)
        displayname = displayname_match.group(1) if displayname_match else ""
        if not displayname:
            displayname_match = re.search(r'<displayname>(.*?)</displayname>', content)
            displayname = displayname_match.group(1) if displayname_match else "小程序"

        result = f"[小程序] {displayname}"
        if title:
            result += f": {title}"

        return result

    def process_transfer(self, content):
        """处理转账消息"""
        # 提取金额
        fee_match = re.search(r'<feedesc>(.*?)</feedesc>', content)
        fee = fee_match.group(1) if fee_match else "未知金额"

        # 提取转账说明
        des_match = re.search(r'<pay_memo>(.*?)</pay_memo>', content)
        des = des_match.group(1) if des_match else ""

        result = f"[转账] {fee}"
        if des:
            result += f" 备注: {des}"

        return result

    def process_red_packet(self, content):
        """处理红包消息"""
        # 提取红包祝福语
        des_match = re.search(r'<sendertitle>(.*?)</sendertitle>', content)
        if not des_match:
            des_match = re.search(r'<des>(.*?)</des>', content)
        des = des_match.group(1) if des_match else "恭喜发财，大吉大利"

        # 提取红包类型（如果有）
        type_match = re.search(r'<templatename>(.*?)</templatename>', content)
        red_type = type_match.group(1) if type_match else "红包"

        return f"[{red_type}] {des}"

    def process_emoji(self, content):
        """处理表情消息"""
        # 提取表情名称（如果有）
        emoji_match = re.search(r'<title>(.*?)</title>', content)
        emoji = emoji_match.group(1) if emoji_match else "表情"

        return f"[表情] {emoji}"

    def process_app_msg(self, content):
        """处理应用消息"""
        # 尝试提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else ""

        # 尝试提取应用名称
        app_match = re.search(r'<appname>(.*?)</appname>', content)
        app = app_match.group(1) if app_match else "应用"

        result = f"[{app}]"
        if title:
            result += f" {title}"

        return result

    def process_generic(self, content):
        """通用处理方法，尝试提取常见的信息字段"""
        # 提取标题
        title_match = re.search(r'<title>(.*?)</title>', content)
        title = title_match.group(1) if title_match else ""

        # 提取描述
        des_match = re.search(r'<des>(.*?)</des>', content, re.DOTALL)
        des = des_match.group(1).strip() if des_match else ""

        # 提取内容
        content_match = re.search(r'<content>(.*?)</content>', content, re.DOTALL)
        content_text = content_match.group(1) if content_match else ""

        # 组合结果
        result_parts = []
        if title:
            result_parts.append(title)
        if des:
            result_parts.append(des)
        if content_text and content_text not in [title, des]:
            result_parts.append(content_text)

        if result_parts:
            return "\n".join(result_parts)

        # 如果没有提取到任何信息，返回原本的消息即可
        return content

    def on_receive_message(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT
        ]:
            return
        msg: ChatMessage = e_context["context"]["msg"]
        msg.content = self.process_content(msg.content)
        self.add_content(msg)

    def add_content(self, message):
        """添加聊天记录到数据库"""
        try:
            conn = self._get_connection()
            with conn:
                cursor = conn.cursor()
                # 将时间戳转换为字符串格式
                time_str = datetime.fromtimestamp(message.create_time).strftime('%Y-%m-%d %H:%M:%S')
                # 插入数据
                cursor.execute('''
                    INSERT OR IGNORE INTO chat_records (group_id, user_nickname, content, create_time)
                    VALUES (?, ?, ?, ?)
                ''', (
                    message.other_user_id,
                    message.actual_user_nickname,
                    message.content,
                    time_str  # 使用格式化后的时间字符串
                ))
                conn.commit()

                # 删除超过最大记录数的旧记录
                cursor.execute('''
                    DELETE FROM chat_records
                    WHERE group_id = ? AND id NOT IN (
                        SELECT id FROM chat_records
                        WHERE group_id = ?
                        ORDER BY create_time DESC
                        LIMIT ?
                    )
                ''', (message.other_user_id, message.other_user_id, self.max_record_quantity))
                conn.commit()
        except Exception as e:
            logger.error(f"[group_chat_summary]添加聊天记录异常：{e}")
    def get_help_text(self, **kwargs):
        help_text = "总结聊天+数量；例：总结聊天 30"
        return help_text
