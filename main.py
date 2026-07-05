# refactor_mcp.py — 本地重構委派工具
# 跑在你的開發機(M3 Pro,和 LM Studio、程式碼同一台)
# 安裝: pip install "mcp[cli]" openai
# 註冊: claude mcp add local-refactor -- python /絕對路徑/refactor_mcp.py

import csv
import time
import difflib
from pathlib import Path
from mcp.server.fastmcp import FastMCP
from openai import OpenAI

LM_STUDIO = OpenAI(base_url="http://localhost:1234/v1", api_key="lm-studio")
MODEL = "qwen2.5-coder-14b"   # 換成 LM Studio 實際載入的模型 ID
MAX_CHARS = 60_000            # 超過就拒收,避免撐爆本地模型 context
LOG_PATH = Path(__file__).parent / "delegate_log.csv"

def _log(row: dict) -> None:
    is_new = not LOG_PATH.exists()
    with LOG_PATH.open("a", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=list(row.keys()))
        if is_new:
            w.writeheader()
        w.writerow(row)

def _changed_lines(before: str, after: str) -> int:
    """概估被改動的行數,用來看模型是否只動了指令範圍。"""
    sm = difflib.SequenceMatcher(None, before.splitlines(), after.splitlines())
    return sum(max(i2 - i1, j2 - j1) for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal")

mcp = FastMCP("local-refactor")

SYSTEM = (
    "你是程式碼重構工具。只執行指定的機械性轉換,"
    "絕對不改變任何邏輯行為。輸出完整修改後的檔案內容,"
    "不要任何解釋、開場白或 markdown 圍欄。"
)

@mcp.tool()
def delegate_refactor(file_path: str, instruction: str) -> str:
    """將「機械性」程式碼修改外包給本地模型執行,以節省 token。

    僅限以下任務:變數/函式重新命名、抽取函式或元件、補 type 標註、
    import 整理、格式與結構搬移、重複樣板的批次展開。
    禁止用於:任何涉及邏輯判斷、演算法變更、行為修改、
    跨檔案架構調整的工作——這些請自行處理,不要呼叫本工具。

    instruction 請寫成單一、明確、可機械執行的指令。
    結果會直接寫回檔案(原檔備份為 .bak),本工具只回傳簡短摘要。
    呼叫成功後,請自行讀取 git diff 驗收,不要假設結果正確。
    """
    p = Path(file_path)
    if not p.is_file():
        return f"錯誤:找不到檔案 {file_path}"
    src = p.read_text(encoding="utf-8")
    if len(src) > MAX_CHARS:
        return f"錯誤:檔案 {len(src)} 字元超過上限 {MAX_CHARS},請自行處理或先拆分。"

    t0 = time.time()
    resp = LM_STUDIO.chat.completions.create(
        model=MODEL,
        temperature=0,
        messages=[
            {"role": "system", "content": SYSTEM},
            {"role": "user",
             "content": f"指令:{instruction}\n\n=== {p.name} ===\n{src}"},
        ],
    )
    elapsed = round(time.time() - t0, 2)
    new = (resp.choices[0].message.content or "").strip()
    usage = resp.usage  # LM Studio 回傳的 token 用量,不用自己數

    # 防呆:剝掉可能的 markdown 圍欄
    if new.startswith("```"):
        new = new.split("\n", 1)[1].rsplit("```", 1)[0].rstrip()

    base_log = {
        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        "file": str(p),
        "instruction": instruction,
        "model": MODEL,
        "elapsed_sec": elapsed,
        "prompt_tokens": usage.prompt_tokens if usage else "",
        "completion_tokens": usage.completion_tokens if usage else "",
    }

    # 防呆:空輸出或嚴重縮水(疑似截斷/偷懶)一律不落盤
    if not new or len(new) < len(src) * 0.3:
        _log({**base_log, "result": "rejected_bad_output", "changed_lines": ""})
        return "本地模型輸出異常(空白或大幅縮水),檔案未變更,請自行處理這個任務。"

    changed = _changed_lines(src, new)
    p.with_suffix(p.suffix + ".bak").write_text(src, encoding="utf-8")
    p.write_text(new + "\n", encoding="utf-8")
    _log({**base_log, "result": "written", "changed_lines": changed})

    return (
        f"已改寫 {p.name},行數 {src.count(chr(10))} → {new.count(chr(10))}"
        f"(diff 概估 {changed} 行),耗時 {elapsed}s,"
        f"prompt/completion tokens: {base_log['prompt_tokens']}/{base_log['completion_tokens']}。"
        f"原檔備份為 {p.name}.bak。請用 git diff 驗收後刪除備份。"
    )

if __name__ == "__main__":
    mcp.run()
    # print(delegate_refactor('./test.ts', '把這個 function 改成箭頭函式'))