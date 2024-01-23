# -*- coding: utf-8 -*-
import base64
import os
import yaml
import datetime
import threading
from collections import defaultdict
from typing import List
from multiprocessing import Event
import agentscope
from config_uitls import load_user_cfg, save_user_cfg, load_default_cfg
from utils import (
    CheckpointArgs,
    enable_web_ui,
    send_player_msg,
    send_player_input,
    get_chat_msg,
    ResetException,
)

import gradio as gr
import modelscope_gradio_components as mgr

enable_web_ui()


def init_uid_list():
    return []


def check_uuid(uid):
    if not uid or uid == '':
        if os.getenv('MODELSCOPE_ENVIRONMENT') == 'studio':
            raise gr.Error('请登陆后使用! (Please login first)')
        else:
            uid = 'local_user'
    return uid


glb_history_dict = defaultdict(init_uid_list)
glb_signed_user = []
is_init = Event()
MAX_NUM_DISPLAY_MSG = 20


# 图片本地路径转换为 base64 格式
def covert_image_to_base64(image_path):
    # 获得文件后缀名
    ext = image_path.split(".")[-1]
    if ext not in ["gif", "jpeg", "png"]:
        ext = "jpeg"

    with open(image_path, "rb") as image_file:
        # Read the file
        encoded_string = base64.b64encode(image_file.read())

        # Convert bytes to string
        base64_data = encoded_string.decode("utf-8")

        # 生成base64编码的地址
        base64_url = f"data:image/{ext};base64,{base64_data}"
        return base64_url


def format_cover_html(config: dict, bot_avatar_path="assets/bg.png"):
    image_src = covert_image_to_base64(bot_avatar_path)
    return f"""
<div class="bot_cover">
    <div class="bot_avatar">
        <img src={image_src} />
    </div>
    <div class="bot_name">{config.get("name", "经营餐厅")}</div>
    <div class="bot_desp">{config.get("description", "快来经营你的餐厅吧")}</div>
</div>
"""


def export_chat_history(uid):
    uid = check_uuid(uid)
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    export_filename = f"chat_history_{timestamp}.txt"

    with open(export_filename, "w", encoding="utf-8") as file:
        for role, message in glb_history_dict[uid]:
            file.write(f"{role}: {message}\n")

    return gr.update(value=export_filename, visible=True)


def get_chat(uid) -> List[List]:
    """Load the chat info from the queue, and put it into the history

    Returns:
        `List[List]`: The parsed history, list of tuple, [(role, msg), ...]

    """
    uid = check_uuid(uid)
    global glb_history_dict
    line = get_chat_msg(uid=uid)
    if line is not None:
        glb_history_dict[uid] += [line]
    return glb_history_dict[uid][-MAX_NUM_DISPLAY_MSG:]


def fn_choice(data: gr.EventData, uid):
    uid = check_uuid(uid)
    send_player_input(data._data["value"], uid=uid)


if __name__ == "__main__":


    def init_game():
        if not is_init.is_set():
            TONGYI_CONFIG = {
                "type": "tongyi",
                "name": "tongyi_model",
                "model_name": "qwen-max-1201",
                "api_key": os.environ.get("TONGYI_API_KEY"),
            }
            agentscope.init(model_configs=[TONGYI_CONFIG], logger_level="INFO")
            is_init.set()

    def check_for_new_session(uid):
        uid = check_uuid(uid)
        if uid not in glb_signed_user:
            glb_signed_user.append(uid)
            print("==========Signed User==========")
            print(f"Total number of users: {len(glb_signed_user)}")
            game_thread = threading.Thread(target=start_game, args=(uid,))
            game_thread.start()

    def start_game(uid):
        is_init.wait()
        uid = check_uuid(uid)
        with open("./config/game_config.yaml", "r", encoding="utf-8") as file:
            GAME_CONFIG = yaml.safe_load(file)

        args = CheckpointArgs()
        args.game_config = GAME_CONFIG
        args.uid = uid
        from main import main

        while True:
            try:
                main(args)
            except ResetException:
                print("重置成功")

    with gr.Blocks(css="assets/app.css") as demo:
        uuid = gr.Textbox(label='modelscope_uuid', visible=False)

        welcome = {
            'name': '饮食男女',
            'description': '这是一款模拟餐馆经营的文字冒险游戏, 快来开始吧😊',
        }
        tabs = gr.Tabs(visible=True)
        with tabs:
            welcome_tab = gr.Tab('游戏界面', id=0)
            config_tab = gr.Tab('游戏配置', id=1)
            with welcome_tab:
                user_chat_bot_cover = gr.HTML(format_cover_html(welcome))
                with gr.Row():
                    with gr.Column():
                        new_button = gr.Button(value='🚀新的探险', )
                    with gr.Column():
                        resume_button = gr.Button(value='🔥续写情缘', )

        chatbot = mgr.Chatbot(
            label='Dialog',
            show_label=False,
            height=600,
            visible=False,
        )

        with config_tab:
            with gr.Row():
                role_selector = gr.Dropdown(label='选择角色查看或者编辑')
                create_role_button = gr.Button('🆕创建角色')
                del_role_button = gr.Button('🧹删除角色')
                save_role_button = gr.Button('🛄保存角色')
                restore_role_button = gr.Button('🔄恢复默认')
            with gr.Row():
                avatar_file = gr.Image(
                    label='头像',
                    sources=['upload'],
                    interactive=True,
                    type='filepath',
                    scale=1,
                    width=182,
                    height=182,
                )
                with gr.Column(scale=4):
                    role_name = gr.Textbox(label='角色名称',
                                           placeholder='请输入角色名称',
                                           interactive=True)
                    with gr.Row():
                        use_memory = gr.Checkbox(label='记忆功能',
                                                 info='是否开启角色记忆功能')
                        model_name = gr.Textbox(label='模型设置')
            with gr.Accordion(label='角色特征', open=True):
                food_preference = gr.Textbox(label='食物偏好',
                                             placeholder='请输入喜欢的食物')
                background = gr.Textbox(label='背景介绍', placeholder='请输入角色背景')
                hidden_plot = gr.Dataframe(label='隐藏剧情设置',
                                           show_label=True,
                                           datatype=['str', 'str'],
                                           headers=['id', '剧情描述'],
                                           type='array',
                                           wrap=True,
                                           col_count=(2, 'fixed'),
                                           interactive=True)
                plugin_background = gr.Dataframe(label='角色插件隐藏背景设置',
                                                 show_label=True,
                                                 datatype=['str'],
                                                 headers=['角色背景'],
                                                 type='array',
                                                 wrap=True,
                                                 col_count=(1, 'fixed'),
                                                 interactive=True)

        with gr.Row():
            with gr.Column():
                user_chat_input = gr.Textbox(
                    label="user_chat_input",
                    placeholder="想说点什么",
                    show_label=False,
                    interactive=True,
                    visible=False,
                )

        with gr.Column():
            send_button = gr.Button(
                value="📣发送",
                visible=False,
            )

        export = gr.Accordion("导出选项", open=False, visible=False)
        with export:
            with gr.Column():
                export_button = gr.Button("导出完整游戏记录")
                export_output = gr.File(
                    label="下载完整游戏记录",
                    visible=False,
                )

        def send_message(msg, uid):
            uid = check_uuid(uid)
            send_player_input(msg, uid=uid)
            send_player_msg(msg, "你", uid=uid)
            return ""

        return_welcome_button = gr.Button(
            value="↩️返回首页",
            visible=False,
        )

        def send_reset_message(uid):
            uid = check_uuid(uid)
            global glb_history_dict
            glb_history_dict[uid] = init_uid_list()
            send_player_input("**Reset**", uid=uid)
            return ""

        def game_ui():
            visible = True
            invisible = False
            return {
                tabs: gr.Tabs(visible=invisible),
                chatbot: mgr.Chatbot(visible=visible),
                user_chat_input: gr.Text(visible=visible),
                send_button: gr.Button(visible=visible),
                new_button: gr.Button(visible=invisible),
                resume_button: gr.Button(visible=invisible),
                return_welcome_button: gr.Button(visible=visible),
                export: gr.Accordion(visible=visible),
                user_chat_bot_cover: gr.HTML(visible=invisible),
            }

        def welcome_ui():
            visible = True
            invisible = False
            return {
                tabs: gr.Tabs(visible=visible),
                chatbot: mgr.Chatbot(visible=invisible),
                user_chat_input: gr.Text(visible=invisible),
                send_button: gr.Button(visible=invisible),
                new_button: gr.Button(visible=visible),
                resume_button: gr.Button(visible=visible),
                return_welcome_button: gr.Button(visible=invisible),
                export: gr.Accordion(visible=invisible),
                user_chat_bot_cover: gr.HTML(visible=visible),
            }

        def configure_role(name, uid):
            uid = check_uuid(uid)
            roles = load_user_cfg(uid)
            role = None
            for r in roles:
                if r['name'] == name:
                    role = r

            character_setting = role['character_setting']

            hidden_plots = [
                [k, v] for k, v in character_setting['hidden_plot'].items()
            ]
            plugin_backgrounds = [
                [str] for str in character_setting['plugin_background']
            ]
            if role:
                # no role in config
                return {
                    avatar_file: gr.Image(value=role['avatar'],
                                          interactive=True),
                    role_name: role['name'],
                    use_memory: gr.Checkbox(value=role['use_memory']),
                    model_name: role['model'],
                    food_preference: character_setting['food_preference'],
                    background: character_setting['background'],
                    hidden_plot: hidden_plots,
                    plugin_background: plugin_backgrounds,
                }
            else:
                return {
                    avatar_file: gr.Image(value=None, interactive=True),
                    role_name: '',
                    use_memory: gr.Checkbox(label='是否开启记忆功能'),
                    model_name: '',
                    food_preference: '',
                    background: '',
                    hidden_plot: None,
                    plugin_background: None
                }

        role_config_options = [
            avatar_file, role_name, use_memory, model_name, food_preference,
            background, hidden_plot, plugin_background
        ]
        role_selector.change(configure_role,
                             inputs=[role_selector, uuid],
                             outputs=role_config_options)

        def on_config_tab_select(uid):
            uid = check_uuid(uid)
            roles = load_user_cfg(uid)
            role_names = [role['name'] for role in roles]
            if len(role_names) < 1:
                gr.Warning('配置中没有发现角色，可以点击恢复默认')
                return gr.Dropdown()
            return gr.Dropdown(value=role_names[0], choices=role_names)

        def create_role():
            return {
                avatar_file:
                gr.Image(value=None),
                role_name:
                gr.Text(value=None), #, interactive=True),
                use_memory:
                gr.Checkbox(value=None, label='是否开启记忆功能'), #, interactive=True),
                model_name:
                gr.Text(value='tongyi_model'),
                food_preference:
                gr.Text(value=None, ), #interactive=True),
                background:
                gr.Textbox(value=None),# , interactive=True),
                hidden_plot:
                gr.DataFrame(value=[['', '']]),# , interactive=True),
                plugin_background:
                gr.DataFrame(value=[['']]), #, interactive=True)
            }

        def delete_role(role_name, uid):
            uid = check_uuid(uid)
            roles = load_user_cfg(uid)
            del_role = None

            for role in roles:
                if role['name'] == role_name:
                    del_role = role
            if del_role in roles and len(roles) >= 2:
                roles.pop(roles.index(del_role))
            else:
                gr.Warning('至少需要一名角色。')
            save_user_cfg(roles, uid)
            role_names = [role['name'] for role in roles]
            return gr.Dropdown(value=role_names[0], choices=role_names)

        def save_role(avatar_file, role_name, use_memory, model_name,
                      food_preference, background, hidden_plot,
                      plugin_background, uid):
            uid = check_uuid(uid)
            roles = load_user_cfg(uid)
            if role_name == '':
                gr.Warning('必须给一个新角色起一个名字')
                role_names = [role['name'] for role in roles]
                return gr.Dropdown(value=role_names[0], choices=role_names)

            new_role = dict()

            for role in roles:
                if role['name'] == role_name:
                    new_role = role
                    break
            if new_role in roles:
                roles.pop(roles.index(new_role))
            new_role = dict()
            new_role['avatar'] = avatar_file
            new_role['name'] = role_name
            new_role['use_memory'] = use_memory
            new_role['model'] = model_name
            character_setting = new_role.get('character_setting', dict())
            character_setting['food_preference'] = food_preference
            character_setting['background'] = background
            character_setting['hidden_plot'] = {
                it[0]: it[1]
                for it in hidden_plot
            }
            character_setting['plugin_background'] = [
                it[0] for it in plugin_background
            ]
            new_role['character_setting'] = character_setting
            roles.append(new_role)
            save_user_cfg(roles, uid)
            role_names = [role['name'] for role in roles]
            return gr.Dropdown(value=role_name, choices=role_names)

        def restore_default_cfg(uid):
            uid = check_uuid(uid)
            roles = load_default_cfg(uid)
            role_names = [role['name'] for role in roles]
            return gr.Dropdown(value=role_names[0], choices=role_names)

        restore_role_button.click(restore_default_cfg,
                                  inputs=[uuid],
                                  outputs=role_selector)
        del_role_button.click(delete_role,
                              inputs=[role_name, uuid],
                              outputs=[role_selector
                                       ])  #+ role_config_options )
        save_role_button.click(save_role,
                               inputs=role_config_options + [uuid],
                               outputs=role_selector)
        create_role_button.click(create_role, outputs=role_config_options)
        config_tab.select(on_config_tab_select,
                          inputs=[uuid],
                          outputs=role_selector)

        outputs = [
            tabs,
            chatbot,
            user_chat_input,
            send_button,
            new_button,
            resume_button,
            return_welcome_button,
            export,
            user_chat_bot_cover,
        ]

        # submit message
        send_button.click(
            send_message,
            [user_chat_input, uuid],
            user_chat_input,
        )
        user_chat_input.submit(
            send_message,
            [user_chat_input, uuid],
            user_chat_input,
        )

        chatbot.custom(fn=fn_choice, inputs=[uuid])

        # change ui
        new_button.click(game_ui, outputs=outputs)
        resume_button.click(game_ui, outputs=outputs)
        return_welcome_button.click(welcome_ui, outputs=outputs)

        # start game
        new_button.click(send_reset_message, inputs=[uuid])
        resume_button.click(check_for_new_session, inputs=[uuid])

        # export
        export_button.click(export_chat_history, [uuid], export_output)

        # demo.load(update_role_selector, outputs=[role_selector])
        # update chat history
        demo.load(init_game)
        demo.load(check_for_new_session, inputs=[uuid], every=0.1)
        demo.load(get_chat, inputs=[uuid], outputs=chatbot, every=0.5)

    demo.queue()
    demo.launch()
