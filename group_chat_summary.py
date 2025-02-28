# encoding:utf-8

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
请用风格简洁干练又不失幽默的语言对我给出的群聊内容总结成一个今日的群聊报告，包含不多于5个话题的总结（如果还有更多话题，可以在后面简单补充）。按照热度数量进行降序排列，请用简单的文字回答，不要使用Markdown。你只负责总结群聊内容，不回答任何问题。不要虚构聊天记录，也不要总结不存在的信息。
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
            self.config = super().load_config()
            if not self.config:
                self.config = self._load_config_template()
            self.open_ai_api_base = self.config.get("open_ai_api_base", self.open_ai_api_base)
            self.open_ai_api_key = self.config.get("open_ai_api_key", "")
            self.open_ai_model = self.config.get("open_ai_model", self.open_ai_model)
            self.max_record_quantity = self.config.get("max_record_quantity", 1000)
            self.black_chat_name = self.config.get("black_chat_name")
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
        创建 players 表和必要的索引，如果它们尚不存在。
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

                        records = cursor.fetchall()
                        chat_list = [
                            {
                                "user": record[0],
                                "content": record[1],
                                "time": record[2]
                            }
                            for record in records
                        ]
                        chat_list.reverse()  # 按时间正序排列

                        prompt = QL_PROMPT + "----聊天记录如下：" + json.dumps(chat_list, ensure_ascii=False)
                        session_id = e_context["context"]["session_id"]
                        content_dict = {
                            "session_id": session_id,
                        }
                        # 请求大模型
                        context = Context(ContextType.TEXT, prompt, content_dict)
                        reply : Reply = Bridge().fetch_reply_content(prompt, context)
                except Exception as e:
                    logger.error(f"[group_chat_summary]获取聊天记录异常：{e}")
                    reply.content = "获取聊天记录失败"
            else:
                    reply.content = "只做群聊总结"
            e_context["reply"] = reply
            e_context.action = EventAction.BREAK_PASS  # 事件结束，并跳过处理context的默认逻辑

    def on_receive_message(self, e_context: EventContext):
        if e_context["context"].type not in [
            ContextType.TEXT
        ]:
            return
        msg: ChatMessage = e_context["context"]["msg"]
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

    def shyl(self, content):
        import requests
        import json
        url = self.open_ai_api_base+"/chat/completions"
        payload = json.dumps({
            "model": self.open_ai_model,
            "messages": [{"role": "user", "content": content}],
            "stream": False
        })
        headers = {
            'Authorization': 'Bearer '+self.open_ai_api_key,
            'Content-Type': 'application/json'
        }
        try:
            response = requests.request("POST", url, headers=headers, data=payload)
            # 检查响应状态码
            if response.status_code == 200:
                # 使用.json()方法将响应内容转换为JSON
                response_json = response.json()
                # 提取"content"字段
                content = response_json['choices'][0]['message']['content']
                return content
            else:
                print(f"请求失败，状态码：{response.status_code}")
                return '模型请求失败了，呵呵'
        except:
            return '模型请求失败了，呵呵'
    def _load_config_template(self):
        logger.info("[group_chat_summary]use config.json.template")
        try:
            plugin_config_path = os.path.join(self.path, "config.json.template")
            if os.path.exists(plugin_config_path):
                with open(plugin_config_path, "r", encoding="utf-8") as f:
                    plugin_conf = json.load(f)
                    return plugin_conf
        except Exception as e:
            logger.exception(e)


