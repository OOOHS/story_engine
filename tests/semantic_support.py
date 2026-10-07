"""Canned model protocol replies used by integration fixtures."""
import json

def action_reply(prompt):
    if not prompt.startswith("## 行动语义解释"):
        return None
    payload = json.loads(prompt.split("## 行动数据\n", 1)[1])
    intent = payload["intent"]
    speech = {"说老周在河对岸开着钟表铺，而且欠我钱。", "说我能瞬移和凭空变出金山。",
              "说陈叔马上就会来。", "说我叔叔老周在河对岸有钟表铺，给钱就能找到他。",
              "说我有通行令，你们应该相信我。", "说今天只修表，不借钱。"}
    moves = {"前往河对岸寻访传闻中的钟表铺": "钟表铺", "前往钟表铺": "钟表铺",
             "前往传闻中的钟表铺": "钟表铺"}
    waits = {"等待", "等待。", "继续等待。", "等待并观察小屋。", "等待并观察周围。"}
    if intent in speech:
        kind, target = "communicate", ""
    elif intent in moves:
        kind, target = "move", moves[intent]
    elif intent in waits:
        kind, target = "wait", ""
    elif intent == "检查陈叔的身份记录":
        kind, target = "observe", ""
    else:
        kind, target = "interact", ""
    return {"content": json.dumps({"kind": kind, "target": target})}

def narration_check_reply(prompt):
    if prompt.startswith("## 叙述语义校对"):
        return {"content": json.dumps({"valid": True, "issues": []})}
    return None
