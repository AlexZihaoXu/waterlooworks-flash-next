FROM node:22.22.3-alpine3.22@sha256:cd7807368cf24826297cbad5dca1a44972ccfd770647db52a8c7589eb4599ac8 AS dependencies
WORKDIR /app
COPY package.json package-lock.json ./
COPY frontend/package.json ./frontend/package.json
RUN npm ci

FROM dependencies AS dev
COPY frontend ./frontend
EXPOSE 5173
CMD ["npm", "run", "dev", "--workspace", "frontend", "--", "--host", "0.0.0.0"]

FROM dependencies AS build
COPY frontend ./frontend
RUN npm run build --workspace frontend

FROM nginxinc/nginx-unprivileged:1.28.2-alpine3.23@sha256:7377697a821c131a924a7105fafbe7414db4e9fcc77a6f08f776f33f141ec3f8 AS prod
COPY --from=build /app/frontend/dist /usr/share/nginx/html
COPY deployment/nginx.conf.template /etc/nginx/nginx.conf.template
COPY deployment/frontend-entrypoint.sh /usr/local/bin/frontend-entrypoint
USER root
RUN chmod 0555 /usr/local/bin/frontend-entrypoint
USER 101:101
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/frontend-entrypoint"]
