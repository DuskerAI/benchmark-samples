.PHONY: all
all:
	erlc -W0 -o /app/src /app/src/ssh_policy.erl
	printf '#!/bin/sh\nexec erl -noshell -pa /app/src -s ssh_policy main -extra "$$@"\n' > /app/ssh-policy
	chmod 755 /app/ssh-policy
