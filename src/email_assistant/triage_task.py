"""Agent responsible for triaging the email, can either ignore it, try to respond, or notify user."""

from langchain_core.messages import AIMessage
from langchain_core.runnables import RunnableConfig
from langchain_groq import ChatGroq
from langchain_core.messages import RemoveMessage
from langgraph.store.base import BaseStore
from langgraph.func import task

from src.schemas import (
    State,
    TriageInput,
    RespondTo,
)
from src.email_assistant.fewshot import get_few_shot_examples

# from src.email_assistant.config import get_config
from src.email_assistant.configuration import Configuration


triage_prompt = """You are {full_name}'s executive assistant, dedicated to maximizing {name}'s efficiency.  

{background}  

{name} receives a high volume of emails. Your task is to categorize the email below and determine the appropriate action.  

**Do NOT require a response:**  
{triage_no}  

**Require a response:**  
{triage_email}  

**Important but no response needed (notify {name} instead):**  
{triage_notify}  

Respond with:  
- `no` → If no response or notification is needed.  
- `email` → If {name} should respond via email.  
- `notify` → If {name} should be informed but no email reply is needed.  

When in doubt, choose `notify`—you will improve over time.  

{fewshotexamples}  

**Classify the following email thread:**  

From: {author}  
To: {to}  
Subject: {subject}  

{email_thread}"""


# @task(name="triage_input")
async def triage_input(state: State, config: RunnableConfig, store: BaseStore):
    """AI Email Assistant workflow"""
    print(f"\n{'='*50}\n triage_input \n{'='*50}\n")
    print(f"triage_input: {state}")

    # model = config["configurable"].get("triage_model", "gemma2-9b-it")
    configuration = Configuration.from_runnable_config(config)
    model = configuration.triage_model

    llm = ChatGroq(model=model, temperature=0)

    examples = await get_few_shot_examples(state["email"], store, config)
    # prompt_config = get_config(config)
    prompt_config = configuration.config_yaml

    input_message = triage_prompt.format(
        email_thread=state["email"]["page_content"],
        author=state["email"]["from_email"],
        to=state["email"].get("to_email", ""),
        subject=state["email"]["subject"],
        fewshotexamples=examples,
        name=prompt_config["name"],
        full_name=prompt_config["full_name"],
        background=prompt_config["background"],
        triage_no=prompt_config["triage_no"],
        triage_email=prompt_config["triage_email"],
        triage_notify=prompt_config["triage_notify"],
    )
    model = llm.with_structured_output(RespondTo).bind(
        tool_choice={"type": "function", "function": {"name": "RespondTo"}}
    )
    response = await model.ainvoke(input_message)
    # if len(state["messages"]) > 0:
    #     delete_messages = [RemoveMessage(id=m.id) for m in state["messages"]]
    #     return {"triage": response, "messages": delete_messages}
    # else:
    return {"triage": response, "messages": [AIMessage(content=f"{response}")]}
