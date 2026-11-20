APP=/app/rpki-audit
SRC=/app/src/rpki_audit.ml

.PHONY: all clean

all: $(APP)

$(APP): $(SRC)
	ocamlc -w -27 -o $(APP) unix.cma $(SRC)

clean:
	rm -f $(APP) /app/src/*.cmi /app/src/*.cmo
