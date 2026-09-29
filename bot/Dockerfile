FROM node:24-bookworm-slim

WORKDIR /app

COPY package*.json ./
RUN npm ci --omit=dev

COPY bot.mjs ./
COPY chat-flow.mjs ./
COPY russian_trusted_root_ca_pem.crt ./

ENV NODE_EXTRA_CA_CERTS=/app/russian_trusted_root_ca_pem.crt

USER node

CMD ["node", "bot.mjs"]
