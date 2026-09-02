# syntax=docker/dockerfile:1.7
#
# CrimeLink console: build the Vite bundle, then serve it from nginx.  The build
# stage is discarded, so the shipped image contains no toolchain.

FROM node:22.11.0-alpine AS build

WORKDIR /build
COPY package.json ./
RUN npm install --no-audit --no-fund
COPY . ./
RUN npm run build

FROM nginx:1.27.3-alpine AS runtime

# Drop the stock site; CrimeLink ships exactly one server block.
RUN rm -f /etc/nginx/conf.d/default.conf

COPY nginx.conf /etc/nginx/conf.d/crimelink.conf
COPY --from=build /build/dist /usr/share/nginx/html

RUN mkdir -p /var/cache/nginx /var/log/nginx \
 && chown -R nginx:nginx /var/cache/nginx /var/log/nginx /usr/share/nginx/html

EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
