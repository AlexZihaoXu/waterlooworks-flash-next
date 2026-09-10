FROM node:22.22.3-alpine3.22 AS dependencies
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

FROM nginxinc/nginx-unprivileged:1.28.2-alpine3.23 AS prod
COPY --from=build /app/frontend/dist /usr/share/nginx/html
COPY deployment/nginx.conf.template /etc/nginx/nginx.conf.template
COPY deployment/frontend-entrypoint.sh /usr/local/bin/frontend-entrypoint
USER root
RUN chmod 0555 /usr/local/bin/frontend-entrypoint
USER 101:101
EXPOSE 8080
ENTRYPOINT ["/usr/local/bin/frontend-entrypoint"]
