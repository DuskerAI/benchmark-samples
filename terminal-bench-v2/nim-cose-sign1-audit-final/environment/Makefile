NIM ?= nim

all: /app/coseaudit/bin/coseaudit

/app/coseaudit/bin/coseaudit: src/main.nim
	mkdir -p /app/coseaudit/bin
	$(NIM) c --hints:off --warnings:off -d:release -o:/app/coseaudit/bin/coseaudit src/main.nim

clean:
	rm -rf /app/coseaudit/bin src/main
