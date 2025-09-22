FROM node:20.18.0 AS build
WORKDIR /inimatic
ARG BUILD_SCRIPT
COPY package*.json ./
RUN npm install --legacy-peer-deps
COPY ./ /inimatic
RUN npm run ${BUILD_SCRIPT}
FROM nginx:latest
COPY --from=build /inimatic/www /usr/share/nginx/html
EXPOSE 8081
