FROM python:3.11-slim

WORKDIR /app

COPY . /app

RUN apt-get update && \
    apt-get install -y curl gnupg && \
    curl -sL https://deb.nodesource.com/setup_20.x  | bash - && \
    apt-get install -y nodejs

RUN pip install --no-cache-dir -r requirements.txt -r requirements-web.txt

RUN apt-get update && \
    apt-get install -y ffmpeg tzdata && \
    ln -fs /usr/share/zoneinfo/Asia/Shanghai /etc/localtime && \
    dpkg-reconfigure -f noninteractive tzdata

# MODE=cli (default): command line recorder, same as before.
# MODE=web: web dashboard on DLR_WEB_PORT; DLR_WEB_PASSWORD is required.
ENV MODE=cli \
    DLR_WEB_PORT=8686
EXPOSE 8686

CMD ["sh", "-c", "if [ \"$MODE\" = \"web\" ]; then exec python web_server.py --host 0.0.0.0; else exec python main.py; fi"]
