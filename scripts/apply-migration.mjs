import { readFile } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const migration = path.join(root, 'infra/postgres/001_core.sql');
const databaseUrl = process.env.DATABASE_URL;

if (!databaseUrl) {
  console.error('Migration not applied: DATABASE_URL is not set.');
  console.error(`Prepared migration: ${migration}`);
  process.exit(2);
}

const sql = await readFile(migration, 'utf8');
try {
  const { Client } = await import('pg');
  const client = new Client({ connectionString: databaseUrl });
  await client.connect();
  await client.query(sql);
  await client.end();
  console.log(`Migration applied: ${migration}`);
} catch (error) {
  console.error(`Migration not applied: ${error.message}`);
  process.exit(1);
}
