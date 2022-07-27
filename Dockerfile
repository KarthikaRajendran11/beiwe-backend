FROM python:3.8.3-alpine

ENV PYTHONDONTWRITEBYTECODE 1

ENV PYTHONUNBUFFERED 1

ENV DOMAIN_NAME localhost://8000

ENV FLASK_SECRET_KEY test

ENV S3_BUCKET beiwe-backend

ENV SYSADMIN_EMAILS krajendran@adentro.com

ENV RDS_DB_NAME mentalhealthco

ENV RDS_USERNAME postgres

ENV RDS_PASSWORD kebDeR7QfCsK4VZg9XiqzHC4

ENV RDS_HOSTNAME mental-health-co-reporting.cqnhjzkxsrgy.us-east-1.rds.amazonaws.com

ENV BEIWE_SERVER_AWS_ACCESS_KEY_ID AKIAY6DIIXJOYUXLBKRL

ENV BEIWE_SERVER_AWS_SECRET_ACCESS_KEY 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

ENV REGION_NAME us-east-1

WORKDIR /usr/src/app

COPY ./requirements.txt /usr/src/app

COPY . /usr/src/app

RUN pip3 install --upgrade pip setuptools wheel

RUN apk add g++ linux-headers

RUN \
 apk add python3-dev build-base linux-headers pcre-dev && \
 apk add --no-cache python3 postgresql-libs && \
 apk add --no-cache --virtual .build-deps gcc python3-dev musl-dev postgresql-dev && \
 python3 -m pip install -r requirements.txt --no-cache-dir && \
 apk --purge del .build-deps

 # find a way to install rabbit MQ

EXPOSE 8000

CMD ["python", "manage.py", "runserver", "0.0.0.0:8000"]
