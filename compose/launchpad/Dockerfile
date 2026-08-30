FROM node:26-alpine AS terminal-assets
WORKDIR /assets
COPY package.json package-lock.json ./
RUN npm ci

FROM python:3.13.5-alpine3.22

RUN apk add --no-cache docker-cli git openssh-client

WORKDIR /app
COPY app/ /app/
COPY --from=terminal-assets /assets/node_modules/@xterm/xterm/css/xterm.css /app/static/xterm.css
COPY --from=terminal-assets /assets/node_modules/@xterm/xterm/lib/xterm.mjs /app/static/xterm.mjs
COPY --from=terminal-assets /assets/node_modules/@xterm/addon-fit/lib/addon-fit.mjs /app/static/addon-fit.mjs

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

CMD ["python", "/app/launchpad.py"]
