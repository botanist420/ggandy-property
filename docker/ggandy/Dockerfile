FROM odoo:19.0

USER root

# 用獨立 venv 安裝 Notebook 工具，同時讀得到 Odoo 的系統套件，
# 避免 pip 覆蓋官方映像內由 Debian 管理的 Python 套件。
RUN apt-get update \
    && apt-get install -y --no-install-recommends python3-venv \
    && usermod --shell /bin/bash odoo \
    && rm -rf /var/lib/apt/lists/* \
    && python3 -m venv --system-site-packages /opt/ggandy-notebook-venv \
    && /opt/ggandy-notebook-venv/bin/python -m pip install \
        --no-cache-dir \
        ipykernel==6.29.5

ENV PATH="/opt/ggandy-notebook-venv/bin:${PATH}"

USER odoo
