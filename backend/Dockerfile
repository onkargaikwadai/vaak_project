FROM python:3.12-slim
WORKDIR /app
COPY . /app
RUN pip install --no-cache-dir .
ENV VAAK_ENV=production
EXPOSE 8080
CMD ["vaak-server"]
