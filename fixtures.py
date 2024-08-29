"""
.fixture Format
---
name: <Fixture Name 1>
address: <DMX Start Address>
<Channel Name>
...
<Channel N Name>

# Optional comments

name: <Fixture 2 Name>
address: <DMX Start Address>
<Channel 1 Name>
...
<Channel N Name>
"""
import os
import logging

logger = logging.getLogger(__name__)


class Fixture:
    def __init__(self, name, channels, address):
        self.name = name
        self.channels = channels
        self.address = address


FIXTURE_DIR = "fixtures"

FIXTURES = []


def parse_fixture(filepath):
    logger.info("Reading %s", filepath)
    fixtures = []
    with open(filepath, "r") as f:
        lines = f.readlines()
        if len(lines) <= 2:
            return

        parse_state = "searching_name"
        name = None
        address = None
        channels = []

        for line in lines:
            line = line.strip().split("#")[0]
            if not line:
                continue
            if parse_state == "searching_name":
                if line.startswith("name:"):
                    name = line.split(":")[-1].strip()
                    parse_state = "searching_address"
            elif parse_state == "searching_address":
                if line.startswith("address:"):
                    address = line.split(":")[-1].strip()
                    try:
                        address = int(address)
                    except:
                        raise RuntimeError("Invalid fixture file")
                    parse_state = "collecting_channel_names"
            elif parse_state == "collecting_channel_names":
                if line.startswith("name:"):
                    if name and address and channels:
                        fixtures.append(Fixture(name, channels, address))
                    name = line.split(":")[-1].strip()
                    address = None
                    channels = []
                    parse_state = "searching_address"
                elif line.startswith("address:"):
                    raise RuntimeError("Invalid fixture file")
                else:
                    channels.append(line.strip().lower().replace(" ", "_"))

        if name and address and channels:
            logger.info("Successfully loaded %s", name)
            fixtures.append(Fixture(name, channels, address))
        return fixtures


for fixture_filename in os.listdir(FIXTURE_DIR):
    fullpath = os.path.join(FIXTURE_DIR, fixture_filename)
    fixtures = parse_fixture(fullpath)
    if not fixtures:
        print(f"Invalid fixture file {fullpath}")
    else:
        for fixture in fixtures:
            FIXTURES.append(fixture)
