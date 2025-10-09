# -*- coding: utf-8 -*-
"""run web ui"""
import argparse
import os
import sys
import threading
import time
from collections import defaultdict
from typing import Optional, Callable, Any, Union
import traceback

from pydantic import BaseModel

from ...agent._user_agent import UserAgent
from ...agent._agent import AgentBase
from ...agent._user_input import (
    UserInputBase,
    UserInputData,
)
from ...message import TextBlock, Msg

try:
    import gradio as gr
except ImportError:
    gr = None

try:
    import modelscope_studio as mgr
except ImportError:
    mgr = None

from .utils import (
    send_player_input,
    get_chat_msg,
    SYS_MSG_PREFIX,
    ResetException,
    check_uuid,
    send_msg,
    generate_image_from_name,
    audio2text,
    send_reset_msg,
    thread_local_data,
    cycle_dots,
    get_reset_msg,
    get_player_input,
)
from .constants import _SPEAK

MAX_NUM_DISPLAY_MSG = 20
FAIL_COUNT_DOWN = 30


def init_uid_list() -> list:
    """Initialize an empty list for storing user IDs."""
    return []


glb_history_dict = defaultdict(init_uid_list)
glb_doing_signal_dict = defaultdict(init_uid_list)
glb_signed_user = []


def reset_glb_var(uid: str) -> None:
    """Reset global variables for a given user ID."""
    global glb_history_dict
    global glb_doing_signal_dict
    glb_history_dict[uid] = init_uid_list()
    glb_doing_signal_dict[uid] = init_uid_list()


def get_chat(uid: str) -> list[list]:
    """Retrieve chat messages for a given user ID."""
    uid = check_uuid(uid)
    global glb_history_dict
    global glb_doing_signal_dict

    line = get_chat_msg(uid=uid)
    # TODO: Optimize the display effect, currently there is a problem of
    #  output display jumping
    if line:
        if line[1] and line[1]["text"] == _SPEAK:
            line[1]["text"] = ""
            glb_doing_signal_dict[uid] = line
        else:
            glb_history_dict[uid] += [line]
            glb_doing_signal_dict[uid] = []
    dial_msg = []
    for line in glb_history_dict[uid]:
        _, msg = line
        if isinstance(msg, dict):
            dial_msg.append(line)
        else:
            # User chat, format: (msg, None)
            dial_msg.append(line)
    if glb_doing_signal_dict[uid]:
        if glb_doing_signal_dict[uid][1]:
            text = cycle_dots(glb_doing_signal_dict[uid][1]["text"])
            glb_doing_signal_dict[uid][1]["text"] = text
            glb_doing_signal_dict[uid][1]["id"] = str(time.time())
            glb_doing_signal_dict[uid][1]["flushing"] = False

        dial_msg.append(glb_doing_signal_dict[uid])
    return dial_msg[-MAX_NUM_DISPLAY_MSG:]


def send_audio(audio_term: str, uid: str) -> None:
    """Convert audio input to text and send as a chat message."""
    uid = check_uuid(uid)
    content = audio2text(audio_path=audio_term)
    send_player_input(content, uid=uid)
    msg = f"""{content}
    <audio src="{audio_term}"></audio>"""
    send_msg(msg, is_player=True, role="Me", uid=uid, avatar=None)


def send_image(image_term: str, uid: str) -> None:
    """Send an image as a chat message."""
    uid = check_uuid(uid)
    send_player_input(image_term, uid=uid)

    msg = f"""<img src="{image_term}"></img>"""
    avatar = generate_image_from_name("Me")
    send_msg(msg, is_player=True, role="Me", uid=uid, avatar=avatar)


def send_message(msg: str, uid: str) -> str:
    """Send a generic message to the player."""
    uid = check_uuid(uid)
    send_player_input(msg, uid=uid)
    avatar = generate_image_from_name("Me")
    send_msg(msg, is_player=True, role="Me", uid=uid, avatar=avatar)
    return ""


def fn_choice(data: gr.EventData, uid: str) -> None:
    """Handle a selection event from the chatbot interface."""
    uid = check_uuid(uid)
    # pylint: disable=protected-access
    send_player_input(data._data["value"], uid=uid)


def import_function_from_path(
    module_path: str,
    function_name: str,
    module_name: Optional[str] = None,
) -> Callable:
    """Import a function from the given module path."""
    import importlib.util

    script_dir = os.path.dirname(os.path.abspath(module_path))

    # Temporarily add a script directory to sys.path
    original_sys_path = sys.path[:]
    sys.path.insert(0, script_dir)

    try:
        # If a module name is not provided, you can use the filename (
        # without extension) as the module name
        if module_name is None:
            module_name = os.path.splitext(os.path.basename(module_path))[0]
        # Creating module specifications and loading modules
        spec = importlib.util.spec_from_file_location(
            module_name,
            module_path,
        )
        if spec is not None:
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            # Getting a function from a module
            function = getattr(module, function_name)
        else:
            raise ImportError(
                f"Could not find module spec for {module_name} at"
                f" {module_path}",
            )
    except AttributeError as exc:
        raise AttributeError(
            f"The module '{module_name}' does not have a function named '"
            f"{function_name}'. Please put your code in the main function, "
            f"read README.md for details.",
        ) from exc
    finally:
        # Restore the original sys.path
        sys.path = original_sys_path

    return function


class _GradioUserInput(UserInputBase):
    """A local gradio user input class, which asks for and receives user input
    from the gradio web UI.

    .. note:: The current gradio web UI only supports one user agent, doesn't
     support distribution mode, and structured input.
    """

    def __init__(self, uid: str, timeout: int = 60) -> None:
        """The gradio user input initialization.

        Args:
            uid (`str`):
                The identity of the user.
            timeout (`str`):
                The timeout for the user input in seconds. Defaults to 60.
        """
        self.uid = uid
        self.timeout = timeout

    def __call__(
        self,
        agent_id: str,
        agent_name: str,
        *args: Any,
        structured_schema: Optional[BaseModel] = None,
        **kwargs: Any,
    ) -> UserInputData:
        """The input method for Gradio."""
        get_reset_msg(uid=self.uid)
        content = get_player_input(
            timeout=self.timeout,
            uid=self.uid,
        )
        return UserInputData(
            blocks_input=[TextBlock(type="text", text=content)],
            structured_input=None,
        )


# pylint: disable=too-many-statements
def run_app() -> None:
    """Entry point for the web UI application."""
    assert gr is not None, "Please install [full] version of AgentScope."

    parser = argparse.ArgumentParser()
    parser.add_argument("script", type=str, help="Script file to run")
    args = parser.parse_args()

    # Make sure script_path is an absolute path
    script_path = os.path.abspath(args.script)

    # Get the directory where the script is located
    script_dir = os.path.dirname(script_path)
    # Save the current working directory
    # Change the current working directory to the directory where
    os.chdir(script_dir)

    def start_game(uid: str) -> None:
        """Start the main game loop."""
        thread_local_data.uid = uid
        if script_path.endswith(".py"):
            main = import_function_from_path(script_path, "main")
        elif script_path.endswith(".json"):
            from agentscope.web.workstation.workflow import (
                start_workflow,
                load_config,
            )

            config = load_config(script_path)
            main = lambda: start_workflow(config)
        else:
            raise ValueError(f"Unrecognized file formats: {script_path}")

        # Register user input method and agent hook here
        UserAgent.override_class_input_method(
            _GradioUserInput(uid=uid),
        )

        # Use a hook to forward messages to gradio web interface
        def gradio_pre_speak_hook(
            self: AgentBase,
            msg: Msg,
            stream: bool,
            last: bool,
        ) -> Union[Msg, None]:
            """Hook to send messages to gradio web interface.

            TODO: support streaming output
            """
            if stream and not last:
                return None

            # User Agent doesn't need the pre_speak_hook, because gradio will
            # print the input instead
            if isinstance(self, UserAgent):
                return None

            get_reset_msg(uid=uid)
            avatar = generate_image_from_name(
                msg.name,
            )

            content = msg.get_text_content() or ""
            flushing = True
            if isinstance(msg.content, list):
                for block in msg.content:
                    if block.get("type") == "image":
                        content += f"\n<img src='{block.get('url')}'/>"
                    elif block.get("type") == "audio":
                        content += (
                            f"\n<audio src='{block.get('url')}' controls/>"
                        )
                    elif block.get("type") == "video":
                        content += (
                            f"\n<video src='{block.get('url')}' controls/>"
                        )
                    elif block.get("type") == "file":
                        content += (
                            f"\n<a href='{block.get('url')}'>"
                            f"{block.get('url')}</a>"
                        )

            send_msg(
                content,
                role=msg.name,
                uid=uid,
                flushing=flushing,
                avatar=avatar,
            )

            return None

        AgentBase.register_class_hook(
            "pre_speak",
            "gradio_pre_speak_hook",
            gradio_pre_speak_hook,
        )

        while True:
            try:
                main()
            except ResetException:
                print(f"Reset Successfully：{uid} ")
            except Exception as e:
                trace_info = "".join(
                    traceback.TracebackException.from_exception(e).format(),
                )
                for i in range(FAIL_COUNT_DOWN, 0, -1):
                    send_msg(
                        f"{SYS_MSG_PREFIX} error {trace_info}, reboot "
                        f"in {i} seconds",
                        uid=uid,
                    )
                    time.sleep(1)
            reset_glb_var(uid)

    def check_for_new_session(uid: str) -> None:
        """
        Check for a new user session and start a game thread if necessary.
        """
        uid = check_uuid(uid)
        if uid not in glb_signed_user:
            glb_signed_user.append(uid)
            print("==========Signed User==========")
            print(f"Total number of users: {len(glb_signed_user)}")
            run_thread = threading.Thread(
                target=start_game,
                args=(uid,),
            )
            run_thread.start()

    with gr.Blocks() as demo:
        warning_html_code = """
                        <div class="hint" style="text-align:
                        center;background-color: rgba(255, 255, 0, 0.15);
                        padding: 10px; margin: 10px; border-radius: 5px;
                        border: 1px solid #ffcc00;">
                        <p>If you want to start over, please click the
                        <strong>reset</strong>
                        button and <strong>refresh</strong> the page</p>
                        </div>
                        """
        gr.HTML(warning_html_code)
        uuid = gr.Textbox(label="modelscope_uuid", visible=False)

        with gr.Row():
            chatbot = mgr.Chatbot(
                label="Dialog",
                show_label=False,
                bubble_full_width=False,
                visible=True,
            )

        with gr.Column():
            user_chat_input = gr.Textbox(
                label="user_chat_input",
                placeholder="Say something here",
                show_label=False,
            )
            send_button = gr.Button(value="📣Send")
        with gr.Row():
            audio = gr.Accordion("Audio input", open=False)
            with audio:
                audio_term = gr.Audio(
                    visible=True,
                    type="filepath",
                    format="wav",
                )
                submit_audio_button = gr.Button(value="Send Audio")
            image = gr.Accordion("Image input", open=False)
            with image:
                image_term = gr.Image(
                    visible=True,
                    height=300,
                    interactive=True,
                    type="filepath",
                )
                submit_image_button = gr.Button(value="Send Image")
        with gr.Column():
            reset_button = gr.Button(value="Reset")

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

        submit_audio_button.click(
            send_audio,
            inputs=[audio_term, uuid],
            outputs=[audio_term],
        )

        submit_image_button.click(
            send_image,
            inputs=[image_term, uuid],
            outputs=[image_term],
        )

        reset_button.click(send_reset_msg, inputs=[uuid])

        chatbot.custom(fn=fn_choice, inputs=[uuid])

        demo.load(
            check_for_new_session,
            inputs=[uuid],
            every=0.5,
        )

        demo.load(
            get_chat,
            inputs=[uuid],
            outputs=[chatbot],
            every=0.5,
        )
    demo.queue()
    demo.launch()


if __name__ == "__main__":
    run_app()
