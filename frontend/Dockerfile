# Frontend: build the Vite/React app, then serve the static output with nginx.
# VITE_API_BASE_URL is baked into the JS bundle at build time (see
# src/api/client.ts), so it must be passed as a build arg pointing at the
# backend's public URL -- not set as a runtime env var, which would have no effect.
FROM node:20-slim AS build
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
COPY . .
ARG VITE_API_BASE_URL
ENV VITE_API_BASE_URL=${VITE_API_BASE_URL}
RUN npm run build

FROM nginx:1.27-alpine
COPY --from=build /app/dist /usr/share/nginx/html
COPY nginx.conf /etc/nginx/conf.d/default.conf
EXPOSE 80
