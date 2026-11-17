# 检验机构公开报告的多格式原件归属采集

资料目录区分检验报告号与样品身份，页图摘要和 OCR 文本分别留存，OCR 不能覆盖报告原页。

现有项目从冻结来源响应读取资料，由 transport 保证请求与原文字节身份，domain 解释本领域字段，ledger 保存版本和原始收据，view 展示已确认内容与缺口。样例为合成资料，不访问真实监管站或企业系统。

Python 3.11 以上；在项目根目录执行：

    python -m unittest discover -s tests -v
    python -m compileall -q capture_service tests
    python -m capture_service.cli inspect data/normal.json

Dockerfile 提供 Linux 环境；docker build -t lab-reports . 构建，docker run --rm lab-reports 运行样例。

真实报告页图字节在附件读取层尚未取得；OCR 仅是页图对应的识别候选。
