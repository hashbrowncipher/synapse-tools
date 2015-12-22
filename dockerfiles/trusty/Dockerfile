FROM ubuntu:14.04
MAINTAINER John Billings <billings@yelp.com>

RUN rm -f /etc/apt/sources.list.d/proposed.list

# Older versions of dh-virtualenv are buggy and don't.. work
RUN apt-key adv --keyserver keyserver.ubuntu.com --recv-keys 88ADA4A042F8DD13 && \
    echo 'deb http://ppa.launchpad.net/dh-virtualenv/daily/ubuntu trusty main\ndeb-src http://ppa.launchpad.net/dh-virtualenv/daily/ubuntu trusty main' >> /etc/apt/sources.list

RUN apt-get update && apt-get -y install \
	debhelper \
	dpkg-dev \
	libyaml-dev \
	libcurl4-openssl-dev \
	python-dev \
	python-tox \
	python-setuptools \
	dh-virtualenv

WORKDIR /work
