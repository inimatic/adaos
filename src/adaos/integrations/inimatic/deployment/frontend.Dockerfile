FROM node:20.18.0 AS build
WORKDIR /inimatic
ARG BUILD_SCRIPT
COPY package*.json ./
# ⬇️ подавляем nx postinstall/optional бинарники и конфликт peer-deps
ENV npm_config_ignore_scripts=true \
    npm_config_legacy_peer_deps=true \
    npm_config_optional=false \
    npm_config_audit=false \
    npm_config_fund=false \
    NX_BINARY_SKIP_DOWNLOAD=true \
    NX_NATIVE=false \
    CI=1
# если есть lock — ci, иначе install
RUN (npm ci) || (echo "npm ci failed, fallback to npm install" && npm install)
COPY ./ /inimatic
RUN npm run ${BUILD_SCRIPT}
FROM nginx:latest
COPY --from=build /inimatic/www /usr/share/nginx/html
COPY ./deployment/nginx/default.conf /etc/nginx/conf.d/default.conf
EXPOSE 8080
