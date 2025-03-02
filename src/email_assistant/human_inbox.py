"""Parts of the graph that require human input."""

import uuid

from langsmith import traceable
from langgraph.func import task
from langgraph.types import interrupt
from langgraph.store.base import BaseStore
from typing import TypedDict, Literal, Union, Optional
from langgraph_sdk import get_client
from langchain_core.runnables import RunnableConfig

# from src.email_assistant.config import get_config
from src.email_assistant.configuration import Configuration
from src.schemas import EmailData, State, email_template

from src.email_assistant.checkpointer import checkpointer, store

langgraph_client = get_client()


class HumanInterruptConfig(TypedDict):
    allow_ignore: bool
    allow_respond: bool
    allow_edit: bool
    allow_accept: bool


class ActionRequest(TypedDict):
    action: str
    args: dict


class HumanInterrupt(TypedDict):
    action_request: ActionRequest
    config: HumanInterruptConfig
    description: Optional[str]


class HumanResponse(TypedDict):
    type: Literal["accept", "ignore", "response", "edit"]
    args: Union[None, str, ActionRequest]


TEMPLATE = """# {subject}

[Click here to view the email]({url})

**To**: {to}
**From**: {_from}

{page_content}
"""


def _generate_email_markdown(email: EmailData):
    # contents = state["email"]
    contents = email
    return TEMPLATE.format(
        subject=contents["subject"],
        url=f"https://mail.google.com/mail/u/0/#inbox/{contents['id']}",
        to=contents["to_email"],
        _from=contents["from_email"],
        page_content=contents["page_content"],
    )


async def save_email(
    email: EmailData, config: RunnableConfig, store: BaseStore, status: str
):
    namespace = (
        config["configurable"].get("assistant_id", "default"),
        "triage_examples",
    )
    configuration = Configuration.from_runnable_config(config=config)
    # namespace = (
    #     configuration.get("assistant_id", "default"),
    #     "triage_examples",
    # )
    key = email["id"]
    response = await store.aget(namespace, key)
    if response is None:
        data = {"input": email, "triage": status}
        await store.aput(namespace, str(uuid.uuid4()), data)


@traceable
async def notify(state: State, config: RunnableConfig, store: BaseStore):
    print(f"\n{'='*50}\n notify \n{'='*50}\n")
    # prompt_config = get_config(config)
    configuration = Configuration.from_runnable_config(config)
    prompt_config = configuration.config_yaml
    memory = prompt_config["memory"]
    user = prompt_config["name"]

    request: HumanInterrupt = {
        "action_request": {"action": "Notify", "args": {}},
        "config": {
            "allow_ignore": True,
            "allow_respond": True,
            "allow_edit": False,
            "allow_accept": False,
        },
        "description": _generate_email_markdown(state["email"]),
    }

    response = interrupt([request])[0]
    _email_template = email_template.format(
        email_thread=state["email"]["page_content"],
        author=state["email"]["from_email"],
        subject=state["email"]["subject"],
        to=state["email"].get("to_email", ""),
    )

    if response["type"] == "response":
        msg = {"type": "user", "content": response["args"]}

        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    }
                ]
                + state["messages"],
                "feedback": f"{user} gave these instructions: {response['args']}",
                "prompt_types": ["email", "background", "calendar"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "ignore":
        msg = {
            "role": "assistant",
            "content": "",
            "id": str(uuid.uuid4()),
            "tool_calls": [
                {
                    "id": "foo",
                    "name": "Ignore",
                    "args": {"ignore": True},
                }
            ],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="no"
            )
    else:
        raise ValueError(f"Unexpected response: {response}")

    return {"messages": [msg]}


@traceable
async def send_message(state: State, config, store):

    configuration = Configuration.from_runnable_config(config=config)
    prompt_config = configuration.config_yaml

    memory = prompt_config["memory"]
    user = prompt_config["name"]

    tool_call = state["messages"][-1].tool_calls[0]
    request: HumanInterrupt = {
        "action_request": {"action": tool_call["name"], "args": tool_call["args"]},
        "config": {
            "allow_ignore": True,
            "allow_respond": True,
            "allow_edit": False,
            "allow_accept": False,
        },
        "description": _generate_email_markdown(state["email"]),
    }
    print("----  task interrupted here ----")
    response = interrupt([request])[0]

    print()
    print("---- response in send_message ----")
    print(response)
    _email_template = email_template.format(
        email_thread=state["email"]["page_content"],
        author=state["email"]["from_email"],
        subject=state["email"]["subject"],
        to=state["email"].get("to_email", ""),
    )
    if response["type"] == "response":
        msg = {
            "type": "tool",
            "name": tool_call["name"],
            "content": response["args"],
            "tool_call_id": tool_call["id"],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    }
                ]
                + state["messages"],
                "feedback": f"{user} responded in this way: {response['args']}",
                "prompt_types": ["background"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "ignore":
        msg = {
            "role": "assistant",
            "content": "",
            "id": state["messages"][-1].id,
            "tool_calls": [
                {
                    "id": tool_call["id"],
                    "name": "Ignore",
                    "args": {"ignore": True},
                }
            ],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="no"
            )
    else:
        raise ValueError(f"Unexpected response: {response}")
    print()
    print("---- messages in send_message ----")
    print({"messages": [msg]})
    return {"messages": [msg]}


@traceable
async def send_email_draft(state: State, config, store):

    configuration = Configuration.from_runnable_config(config=config)
    prompt_config = configuration.config_yaml

    memory = prompt_config["memory"]
    user = prompt_config["name"]
    print()
    print("state in send_email_draft")
    print(state)
    print()
    tool_call = state["messages"][-1].tool_calls[0]

    request: HumanInterrupt = {
        "action_request": {"action": tool_call["name"], "args": tool_call["args"]},
        "config": {
            "allow_ignore": True,
            "allow_respond": True,
            "allow_edit": True,
            "allow_accept": True,
        },
        "description": _generate_email_markdown(state["email"]),
    }

    response = interrupt([request])[0]

    _email_template = email_template.format(
        email_thread=state["email"]["page_content"],
        author=state["email"]["from_email"],
        subject=state["email"]["subject"],
        to=state["email"].get("to_email", ""),
    )
    if response["type"] == "response":
        msg = {
            "type": "tool",
            "name": tool_call["name"],
            "content": f"Error, {user} interrupted and gave this feedback: {response['args']}",
            "tool_call_id": tool_call["id"],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    }
                ]
                + state["messages"],
                "feedback": f"Error, {user} interrupted and gave this feedback: {response['args']}",
                "prompt_types": ["tone", "email", "background", "calendar"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "ignore":
        msg = {
            "role": "assistant",
            "content": "",
            "id": state["messages"][-1].id,
            "tool_calls": [
                {
                    "id": tool_call["id"],
                    "name": "Ignore",
                    "args": {"ignore": True},
                }
            ],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="no"
            )
    elif response["type"] == "edit":
        msg = {
            "role": "assistant",
            "content": state["messages"][-1].content,
            "id": state["messages"][-1].id,
            "tool_calls": [
                {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "args": response["args"]["args"],
                }
            ],
        }
        if memory:
            corrected = response["args"]["args"]["content"]
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    },
                    {
                        "role": "assistant",
                        "content": state["messages"][-1].tool_calls[0]["args"][
                            "content"
                        ],
                    },
                ],
                "feedback": f"A better response would have been: {corrected}",
                "prompt_types": ["tone", "email", "background", "calendar"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "accept":
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="no"
            )
        return None
    else:
        raise ValueError(f"Unexpected response: {response}")
    return {"messages": [msg]}


@traceable
async def send_cal_invite(state: State, config, store):

    configuration = Configuration.from_runnable_config(config=config)
    prompt_config = configuration.config_yaml

    memory = prompt_config["memory"]
    user = prompt_config["name"]
    tool_call = state["messages"][-1].tool_calls[0]

    request: HumanInterrupt = {
        "action_request": {"action": tool_call["name"], "args": tool_call["args"]},
        "config": {
            "allow_ignore": True,
            "allow_respond": True,
            "allow_edit": True,
            "allow_accept": True,
        },
        "description": _generate_email_markdown(state["email"]),
    }

    print("----  task interrupted here ----")
    response = interrupt([request])[0]

    print()
    print("---- response in send_message ----")
    print(response)
    _email_template = email_template.format(
        email_thread=state["email"]["page_content"],
        author=state["email"]["from_email"],
        subject=state["email"]["subject"],
        to=state["email"].get("to_email", ""),
    )
    if response["type"] == "response":
        msg = {
            "type": "tool",
            "name": tool_call["name"],
            "content": f"Error, {user} interrupted and gave this feedback: {response['args']}",
            "tool_call_id": tool_call["id"],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    }
                ]
                + state["messages"],
                "feedback": f"{user} interrupted gave these instructions: {response['args']}",
                "prompt_types": ["email", "background", "calendar"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            print(f"---- send_cal_invite rewrite state ----")
            print(rewrite_state)
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "ignore":
        msg = {
            "role": "assistant",
            "content": "",
            "id": state["messages"][-1].id,
            "tool_calls": [
                {
                    "id": tool_call["id"],
                    "name": "Ignore",
                    "args": {"ignore": True},
                }
            ],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="no"
            )
    elif response["type"] == "edit":
        msg = {
            "role": "assistant",
            "content": state["messages"][-1].content,
            "id": state["messages"][-1].id,
            "tool_calls": [
                {
                    "id": tool_call["id"],
                    "name": tool_call["name"],
                    "args": response["args"]["args"],
                }
            ],
        }
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
            rewrite_state = {
                "messages": [
                    {
                        "role": "user",
                        "content": f"Draft a response to this email:\n\n{_email_template}",
                    }
                ]
                + state["messages"],
                "feedback": f"{user} interrupted gave these instructions: {response['args']}",
                "prompt_types": ["email", "background", "calendar"],
                "assistant_key": config["configurable"].get("assistant_id", "default"),
            }
            # await langgraph_client.runs.create(
            #     None, "multi_reflection_graph", input=rewrite_state
            # )
    elif response["type"] == "accept":
        if memory:
            await save_email(
                email=state["email"], config=config, store=store, status="email"
            )
        return None
    else:
        raise ValueError(f"Unexpected response: {response}")
    print()
    print("---- messages in send_cal_invite ----")
    print({"messages": [msg]})
    return {"messages": [msg]}
