-- The team's hand checks from check.html: one current answer per fence line, every save
-- kept in the log, and who has a line open right now so two people don't check the same one.

CREATE TABLE answers (
  tile TEXT NOT NULL,
  fence INTEGER NOT NULL,
  n INTEGER,
  parcel_a INTEGER,
  parcel_b INTEGER,
  street_front INTEGER,
  length_m REAL,
  lat REAL,
  lon REAL,
  structure TEXT,
  along TEXT,
  height TEXT,
  water TEXT,
  seen TEXT,
  sure TEXT,
  note TEXT,
  who TEXT,
  at TEXT NOT NULL,
  PRIMARY KEY (tile, fence)
);

CREATE TABLE log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tile TEXT NOT NULL,
  fence INTEGER NOT NULL,
  structure TEXT,
  along TEXT,
  height TEXT,
  water TEXT,
  seen TEXT,
  sure TEXT,
  note TEXT,
  who TEXT,
  at TEXT NOT NULL
);

CREATE TABLE looking (
  tile TEXT NOT NULL,
  fence INTEGER NOT NULL,
  who TEXT,
  at TEXT NOT NULL,
  PRIMARY KEY (tile, fence)
);
