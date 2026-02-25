iostat -w 1 | awk '$1 ~ /^[0-9]/ {n++; if (n==2) {print $3; exit}}'
