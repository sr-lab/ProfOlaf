from rich import print
from rich.prompt import Prompt

def pretty_print(string: str):
    print(string)

def format_color_string(string: str, color: str, style: str):
    style = style + " " if style != "" else ""
    color_tag = f"[{style}{color}]"
    color_end_tag = f"[/{style}{color}]"
    return f"{color_tag}{string}{color_end_tag}"

def prompt_input(string: str):
    return Prompt.ask(string)