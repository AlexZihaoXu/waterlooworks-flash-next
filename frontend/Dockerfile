FROM node:22-alpine AS dependencies
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

FROM nginxinc/nginx-unprivileged:stable-alpine AS prod
COPY --from=build /app/frontend/dist /usr/share/nginx/html
EXPOSE 8080
