# backend.Dockerfile
FROM node:20.18.0
WORKDIR /inimatic_backend

# 1) только манифесты
COPY ./package*.json ./

# 2) env, чтобы nx не падал на postinstall
ENV NX_BINARY_SKIP_DOWNLOAD=true \
    NX_NATIVE=false \
	npm_config_legacy_peer_deps=true \
    npm_config_fund=false \
    npm_config_audit=false \
    CI=1
RUN (npm ci --legacy-peer-deps) || (echo "npm ci failed, falling back to npm install" && npm install --legacy-peer-deps)
# 3) чистая установка (без перетягивания нативных бинари Nx)
RUN npm ci

# 4) код бэкенда
COPY ./backend ./backend

# 5) сборка как и раньше
RUN npm run build:api

EXPOSE 3030
CMD ["npm", "run", "serve:api"]
