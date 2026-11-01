# Container riêng cho công cụ đăng web; không đụng container app.
FROM python:3.12-slim
WORKDIR /app
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 \
    REMBG_HOME=/data/.mo-hinh OMP_NUM_THREADS=4 NUMBA_CACHE_DIR=/tmp/numba
COPY dang_web/requirements.txt /tmp/req.txt
RUN pip install --no-cache-dir -r /tmp/req.txt
COPY dang_web ./dang_web
# Cùng uid 1000 với tài khoản tuan trên Z2 để file trong ~/THANHDUC-WEB không bị khóa quyền.
RUN useradd --system --uid 1000 td
USER td
ENTRYPOINT ["python", "-m", "dang_web"]
