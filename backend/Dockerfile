FROM node:22.12.0-alpine
WORKDIR /app
RUN apk add --no-cache python3 make g++
COPY package.json ./
RUN npm install --omit=dev
COPY db.js groq.js server.js ./
EXPOSE 3001
CMD ["node","server.js"]
