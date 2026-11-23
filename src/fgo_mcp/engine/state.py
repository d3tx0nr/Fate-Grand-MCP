import json
import time
from dataclasses import dataclass, field

@dataclass
class ActionSpec:
    name: str
    args: str = ""      # e.g. "slot 1-3, skill 1-3, target ally 1-3 if needed"
    example: str = ""   # e.g. '{"slot":1,"skill":2}'

    def text(self) -> str:
        s = f"{self.name}({self.args})"
        if self.example:
            s += f" e.g. {self.example}"
        return s

@dataclass
class ScreenState:
    id: int
    type: str
    text: str
    data: dict = field(default_factory=dict)
    actions: list[ActionSpec] = field(default_factory=list)
    needs_creator: str | None = None
    guide: str = ""
    t: float = field(default_factory=time.time)

    def render(self, detail: str = "brief", with_guide: bool = False) -> str:
        lines = [f"screen_id={self.id} screen={self.type}"]
        if with_guide and self.guide:
            lines.append(f"Guide: {self.guide}")
        if self.text:
            lines.append(self.text)
        if self.needs_creator:
            lines.append(f"NEEDS CREATOR: {self.needs_creator}")
        if self.actions:
            action_texts = []
            for a in self.actions:
                action_texts.append(a.text())
            lines.append("Actions: " + " | ".join(action_texts))
        else:
            lines.append("Actions: none on this screen (wait, then fgo_screen again)")
        if detail == "full" and self.data:
            lines.append("data: " + json.dumps(self.data, default=str))
        return "\n".join(lines)