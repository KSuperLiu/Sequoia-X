FROM node:22-alpine AS build
WORKDIR /web
COPY package.json package-lock.json* ./
RUN npm install
COPY . .
RUN npm run build

FROM caddy:2-alpine
COPY Caddyfile /etc/caddy/Caddyfile
COPY --from=build /web/dist /srv
