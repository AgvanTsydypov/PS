#!/usr/bin/env node

/**
 * Скрипт для генерации SESSION_SECRET
 * Использование: node scripts/generate-secret.js
 */

const crypto = require('crypto');

function generateSecret(length = 32) {
  return crypto.randomBytes(length).toString('base64');
}

console.log('\n=== Генератор SESSION_SECRET ===\n');
console.log('Сгенерированный секретный ключ:');
console.log('\n' + generateSecret(32) + '\n');
console.log('Добавьте этот ключ в ваш .env.local файл:');
console.log('SESSION_SECRET=' + generateSecret(32) + '\n');
console.log('⚠️  Не делитесь этим ключом и не коммитьте его в Git!\n');
