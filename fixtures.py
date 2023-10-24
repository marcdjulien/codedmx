import os

class Fixture:
	def __init__(self, name, channels):
		self.name = name
		self.channels = channels

FIXTURE_DIR = "fixtures"

FIXTURES = []

for fixture_filename in os.listdir(FIXTURE_DIR):
	fullpath = os.path.join(FIXTURE_DIR, fixture_filename)
	with open(fullpath, 'r') as f:
		lines = f.readlines()
		if len(lines) <= 1:
			print(f"Invalid fixture file {fullpath}")
			continue
		name = lines[0].strip()
		channels = [line.strip() for line in lines[1::] if line.strip()]
		FIXTURES.append(Fixture(name, channels))