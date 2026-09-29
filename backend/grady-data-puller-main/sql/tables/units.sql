CREATE TABLE IF NOT EXISTS units (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE,
    category TEXT NOT NULL,
    base_unit TEXT NOT NULL,
    multiplier REAL NOT NULL DEFAULT 1.0,
    description TEXT
);

-- Weight units (base: Metric Tons)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Million Metric Tons',             'weight', 'Metric Tons', 1e6,    'Million metric tons'),
    ('Metric Tons',                     'weight', 'Metric Tons', 1,      'Metric tons'),
    ('1000 Metric Tons, Actual Weight', 'weight', 'Metric Tons', 1e3,    'Thousand metric tons, actual weight'),
    ('Thousand Short Tons',             'weight', 'Metric Tons', 907.185,'Thousand short tons (1 short ton = 0.907185 MT)'),
    ('1000 Short Tons, Raw Value',      'weight', 'Metric Tons', 907.185,'Thousand short tons, raw value'),
    ('Million Pounds',                  'weight', 'Metric Tons', 453.592,'Million pounds (1M lbs = 453.592 MT)'),
    ('Billion Pounds',                  'weight', 'Metric Tons', 453592, 'Billion pounds'),
    ('Pounds',                          'weight', 'Metric Tons', 0.000453592, 'Pounds'),
    ('Million Hundredweight',           'weight', 'Metric Tons', 45359.2,'Million hundredweight (1 cwt = 100 lbs)');

-- Volume units (base: Bushels)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Million Bushels',  'volume', 'Bushels', 1e6, 'Million bushels'),
    ('Bushels',          'volume', 'Bushels', 1,   'Bushels');

-- Cotton bale units (base: 480-Pound Bales)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Million 480-Pound Bales', 'bales', '480-Pound Bales', 1e6, 'Million 480-pound bales'),
    ('Million 480 Pound Bales', 'bales', '480-Pound Bales', 1e6, 'Million 480-pound bales (no hyphen)'),
    ('Million 480-lb. Bales',   'bales', '480-Pound Bales', 1e6, 'Million 480-lb bales (abbreviated)');

-- Area units (base: Acres)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Million Acres', 'area', 'Acres', 1e6,  'Million acres'),
    ('mil. acres',    'area', 'Acres', 1e6,  'Million acres (abbreviated)'),
    ('1,000 Acres',   'area', 'Acres', 1e3,  'Thousand acres');

-- Count units (base: Dozen for eggs, Head for livestock)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Million Dozen', 'count', 'Dozen', 1e6, 'Million dozen'),
    ('number',        'count', 'Head',  1,   'Head count');

-- Yield units (rate, not directly convertible across categories)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('bushels/acre',  'yield', 'bushels/acre',  1, 'Bushels per acre'),
    ('pounds/acre',   'yield', 'pounds/acre',   1, 'Pounds per acre');

-- Price units (base: Dollars)
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('$/bu',              'price', '$/bu',      1, 'Dollars per bushel'),
    ('$/cwt',             'price', '$/cwt',     1, 'Dollars per hundredweight'),
    ('$/s.t.',            'price', '$/s.t.',    1, 'Dollars per short ton'),
    ('Dol./cwt',          'price', '$/cwt',     1, 'Dollars per hundredweight (alt format)'),
    ('Dollars Per Pound', 'price', '$/lb',      1, 'Dollars per pound');

-- Ratio / percentage units
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('Percent', 'ratio', 'Percent', 1,   'Percent'),
    ('%',       'ratio', 'Percent', 1,   'Percent (symbol)'),
    ('Years',   'ratio', 'Years',   1,   'Years (stocks-to-use ratio)');

-- Other / special
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('mil.',         'other', 'Million', 1e6, 'Million (unspecified)'),
    ('rough equiv.', 'other', 'rough equiv.', 1, 'Rough equivalent basis');

-- NASS Grain Stocks / Crop Progress units
INSERT OR IGNORE INTO units (name, category, base_unit, multiplier, description) VALUES
    ('1,000 Bushels', 'volume', 'Bushels', 1e3, 'Thousand bushels'),
    ('1,000 Pounds',  'weight', 'Metric Tons', 0.453592, 'Thousand pounds'),
    ('Days',          'other',  'Days',    1, 'Days suitable for fieldwork');
