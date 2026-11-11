"""樣本職缺：把 jobs/ 的拆解結果匯出成可以公開部署的 samples/jobs.json。

為什麼要這一步：網頁版要和「10 份樣本職缺」比對，但 jobs/（職缺原文）和 cache/（含原文片段）都不能上傳。
所以只匯出比對需要的欄位：職稱、公司、年資、每個技能的名稱／類別／必備或加分。
職缺原文（original_text）和工作內容摘要（responsibilities）一律清空。

用法：
    python -m job_agent.samples jobs                  匯出 jobs/ 裡全部（拆解有快取就不花錢）
    python -m job_agent.samples jobs/02.txt jobs/06.txt
"""

import argparse
import json
from pathlib import Path

from job_agent.extract import extract_requirements
from job_agent.schemas import JobRequirements

SAMPLES_FILE = Path("samples/jobs.json")


def strip_job(job: JobRequirements) -> JobRequirements:
    """拿掉職缺原文：original_text 清空、responsibilities 清空。"""
    return job.model_copy(update={
        "requirements": [r.model_copy(update={"original_text": ""}) for r in job.requirements],
        "responsibilities": [],
    })


def export_samples(files: list[Path], out: str | Path = SAMPLES_FILE, client=None) -> Path:
    out = Path(out)
    data = []
    for f in files:
        job, stats = extract_requirements(f, client=client)
        data.append({"job_id": f.stem, "job": strip_job(job).model_dump()})
        cached = "快取" if stats.get("cached") else f"重新拆解 ${stats['cost_usd']:.4f}"
        print(f"{f.name}  {job.title} @ {job.company}  技能 {len(job.requirements)} 個（{cached}）")
    out.parent.mkdir(exist_ok=True)
    out.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def load_samples(path: str | Path = SAMPLES_FILE) -> list[tuple[str, JobRequirements]]:
    """讀 samples/jobs.json → [(職缺編號, JobRequirements)]。"""
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    return [(d["job_id"], JobRequirements.model_validate(d["job"])) for d in data]


def main(argv: list[str] | None = None) -> None:
    from job_agent.__main__ import job_files

    parser = argparse.ArgumentParser(prog="python -m job_agent.samples", description="匯出樣本職缺（不含原文）")
    parser.add_argument("jobs", nargs="+", help="職缺檔案或資料夾")
    parser.add_argument("--out", default=str(SAMPLES_FILE))
    args = parser.parse_args(argv)
    out = export_samples(job_files(args.jobs), args.out)
    print(f"\n已匯出：{out}（請打開檢查裡面沒有職缺原文，再 commit）")


if __name__ == "__main__":
    main()