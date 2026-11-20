GNATMAKE ?= gnatmake
PREFIX ?= /usr/local

.PHONY: all install clean

all: bin/nft_policy

bin/nft_policy: src/nft_policy.adb
	@mkdir -p bin obj
	$(GNATMAKE) -gnat2020 -O2 -D obj -o $@ $<

install: all
	@mkdir -p $(PREFIX)/sbin
	cp bin/nft_policy $(PREFIX)/sbin/nft-policy-tool
	cp wrappers/nft-policy-compile $(PREFIX)/sbin/nft-policy-compile
	cp wrappers/nft-policy-compare $(PREFIX)/sbin/nft-policy-compare
	chmod 755 $(PREFIX)/sbin/nft-policy-tool $(PREFIX)/sbin/nft-policy-compile $(PREFIX)/sbin/nft-policy-compare

clean:
	rm -rf bin obj
