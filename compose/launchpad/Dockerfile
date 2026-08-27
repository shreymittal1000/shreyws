FROM python:3.13.5-alpine3.22

RUN apk add --no-cache docker-cli git openssh-client

WORKDIR /app
COPY app/ /app/

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "/app/launchpad.py"]
