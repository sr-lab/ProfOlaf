import os

with open("confs/seed.txt", "r") as f:
    lines = f.readlines()

with open("confs/seed_processed.txt", "w") as f:
    for line in lines:
        line = line.strip()
        if " - " in line:
            line = line.split(" - ")[0]
        f.write(line + "\n")