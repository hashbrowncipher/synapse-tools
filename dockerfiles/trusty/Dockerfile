FROM ubuntu:14.04
MAINTAINER John Billings <billings@yelp.com>

RUN rm -f /etc/apt/sources.list.d/proposed.list
RUN apt-get update && apt-get -y install \
	debhelper \
	dpkg-dev \
	libyaml-dev \
	libcurl4-openssl-dev \
	python-dev \
	python-tox \
	python-setuptools
# Older versions of dh-virtualenv are buggy and don't.. work
RUN curl http://ppa.launchpad.net/dh-virtualenv/daily/ubuntu/pool/main/d/dh-virtualenv/dh-virtualenv_0.10-0~81~ubuntu14.04.1_all.deb --output dh-virtualenv_0.10-0~81~ubuntu14.04.1_all.deb && \
  dpkg -i dh-virtualenv_0.10-0~81~ubuntu14.04.1_all.deb && rm dh-virtualenv_0.10-0~81~ubuntu14.04.1_all.deb

WORKDIR /work
