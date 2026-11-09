-- Stand-in for the client's payments system of record ("core_payments").
-- We do not own this database; the internal-tools platform gets a READ-ONLY role.
-- Local dev only: credentials below are not secrets.
CREATE TABLE charges (
    charge_id       text PRIMARY KEY,
    customer_name   text NOT NULL,
    customer_email  text NOT NULL,
    amount_minor    bigint NOT NULL CHECK (amount_minor > 0),
    currency        char(3) NOT NULL,
    region          text NOT NULL CHECK (region IN ('US', 'EU')),
    status          text NOT NULL CHECK (status IN ('captured', 'pending', 'voided')),
    captured_at     timestamptz NOT NULL DEFAULT now()
);

INSERT INTO charges (charge_id, customer_name, customer_email, amount_minor, currency, region, status) VALUES
    ('ch_1001', 'Ada Lovelace',      'ada@customer.test',       4500,   'USD', 'US', 'captured'),
    ('ch_1002', 'Grace Hopper',      'grace@customer.test',     25000,  'USD', 'US', 'captured'),
    ('ch_1003', 'Alan Turing',       'alan@customer.test',      120000, 'USD', 'US', 'captured'),
    ('ch_1004', 'Katherine Johnson', 'katherine@customer.test', 9999,   'USD', 'US', 'captured'),
    ('ch_1005', 'Edsger Dijkstra',   'edsger@customer.test',    75000,  'EUR', 'EU', 'captured'),
    ('ch_1006', 'Barbara Liskov',    'barbara@customer.test',   3200,   'EUR', 'EU', 'captured'),
    ('ch_1007', 'Tim Berners-Lee',   'tim@customer.test',       58000,  'EUR', 'EU', 'captured'),
    ('ch_1008', 'Margaret Hamilton', 'margaret@customer.test',  15000,  'USD', 'US', 'captured'),
    ('ch_1009', 'Linus Torvalds',    'linus@customer.test',     2100,   'EUR', 'EU', 'pending');

-- Synthetic history for demos: 300 deterministic charges over the last 30 days (no random()).
INSERT INTO charges (charge_id, customer_name, customer_email, amount_minor, currency, region, status, captured_at)
SELECT
    'ch_' || (2000 + g),
    (ARRAY['Ada','Grace','Alan','Katherine','Edsger','Barbara','Tim','Margaret','Donald','Frances',
           'John','Radia','Ken','Dennis','Hedy','Claude','Shafi','Leslie','Sophie','Niklaus'])[1 + mod(g, 20)]
        || ' ' ||
    (ARRAY['Nakamura','Okafor','Silva','Muller','Rossi','Kowalski','Andersen','Dubois','Haddad','Patel',
           'Garcia','Novak','Jensen','Moreau','Costa','Schmidt','Ivanova','Larsen','Conti','Byrne'])[1 + mod(g * 7, 20)],
    'customer' || g || '@customer.test',
    500 + mod(g * 7919, 149500),
    CASE WHEN mod(g, 3) = 0 THEN 'EUR' ELSE 'USD' END,
    CASE WHEN mod(g, 3) = 0 THEN 'EU' ELSE 'US' END,
    CASE WHEN mod(g, 17) = 0 THEN 'pending' WHEN mod(g, 29) = 0 THEN 'voided' ELSE 'captured' END,
    now() - (mod(g * 137, 30 * 24 * 60)) * interval '1 minute'
FROM generate_series(1, 300) AS g;

DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'tools_reader') THEN
        CREATE ROLE tools_reader LOGIN PASSWORD 'tools_reader_dev';
    END IF;
END
$$;
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM PUBLIC;
GRANT USAGE ON SCHEMA public TO tools_reader;
GRANT SELECT ON charges TO tools_reader;
