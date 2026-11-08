ARG BUILD_FROM
FROM $BUILD_FROM

RUN apk add --no-cache python3 py3-pip && \
    pip3 install --no-cache-dir --break-system-packages websockets==12.0 paho-mqtt==1.6.1

COPY run.py /run.py

CMD [ "python3", "/run.py" ]
