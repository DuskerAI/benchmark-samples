NIM ?= nim
PREFIX ?= /usr/local

all: bin/x509-path-attest

bin/x509-path-attest: src/x509_path_attest.nim
	mkdir -p bin
	$(NIM) c -d:release --hints:off --warnings:off -o:$@ $<

install: bin/x509-path-attest
	mkdir -p $(PREFIX)/sbin
	cp bin/x509-path-attest $(PREFIX)/sbin/x509-path-attest
