# 检验机构公开报告的多格式原件归属采集

资料目录区分检验报告号与样品身份，页图摘要和 OCR 文本分别留存，OCR 不能覆盖报告原页。

现有项目从冻结来源响应读取资料，由 transport 保证请求与原文字节身份，domain 解释本领域字段，ledger 保存版本和原始收据，view 展示已确认内容与缺口。样例为合成资料，不访问真实监管站或企业系统。

Python 3.11 以上；在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q capture_service tests
    python -m capture_service.cli inspect data/normal.json
    python -m capture_service.cli inspect data/org-a.json
    python -m capture_service.cli inspect data/versions.json
    python -m capture_service.cli inspect data/org-a.json --db reports.db --source-collection north
    python -m capture_service.cli inspect data/org-a.json --db reports.db --source-collection south --resume-from 3
    python -m capture_service.cli trace data/versions.json --report R-20 --institution lab-north
    python -m capture_service.cli cat data/org-a.json --entity-type artifact \
        --entity-id lab-north/R-17/1 --version v1 --output page1.png

Dockerfile 提供 Linux 环境；docker build -t lab-reports . 构建，docker run --rm lab-reports 运行样例。

`data/org-a.json` 覆盖两个机构各自的 R-17（同名不同规格）、页图原件、
OCR 候选、已确认与候选通告映射，以及无法核实机构的扫描件；`data/versions.json`
覆盖报告修订、页图撤换和补页。机构目录、原件字节、版本关系与按报告号的
追溯记录都保存在账本中。OCR 仅是页图对应的识别候选，不覆盖原件。
