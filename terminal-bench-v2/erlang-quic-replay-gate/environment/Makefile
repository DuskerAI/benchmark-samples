.PHONY: all clean

all:
	mkdir -p ebin
	erlc -o ebin src/qjson.erl src/quic_gate.erl

clean:
	rm -rf ebin erl_crash.dump
