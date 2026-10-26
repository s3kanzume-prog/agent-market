// Public discovery only: no wallet, signature, work claim, or payment.
import assert from 'node:assert/strict';

const base = 'https://agent-market-826727841930.us-central1.run.app';
let nextId = 1;
async function json(path, body) {
  const response = await fetch(base + path, {
    headers: { Accept: 'application/json, text/event-stream', ...(body ? { 'Content-Type': 'application/json' } : {}) },
    ...(body ? { method: 'POST', body: JSON.stringify(body) } : {}),
    signal: AbortSignal.timeout(30000),
  });
  if (!response.ok) throw new Error(`${path}: HTTP ${response.status}`);
  return response.json();
}
async function rpc(method, params) {
  const reply = await json('/mcp', { jsonrpc: '2.0', id: nextId++, method, params });
  if (reply.error) throw new Error(`${method}: ${reply.error.message}`);
  return reply.result;
}
async function tool(name, args = {}) {
  const result = await rpc('tools/call', { name, arguments: args });
  if (result.isError) throw new Error(`${name}: ${result.content?.find(x => x.type === 'text')?.text}`);
  return JSON.parse(result.content.find(x => x.type === 'text').text);
}

const service = await json('/');
assert.equal(service.network, 'eip155:84532');
const initialized = await rpc('initialize', {
  protocolVersion: '2025-06-18', capabilities: {}, clientInfo: { name: 'agent-market-discovery-example', version: '1.0.0' },
});
assert.ok(initialized.capabilities.tools);
const { tools } = await rpc('tools/list', {});
for (const name of ['work_catalog', 'list_tasks', 'quote_work']) assert.ok(tools.some(t => t.name === name), `Missing ${name}`);
const args = { priceUsd: 0.01, maxListingFeeUsd: 0.001 };
const quote = await tool('quote_work', args);
const restQuote = await json('/work/quote', args);
assert.equal(quote.listingFeeUsd, restQuote.listingFeeUsd);
assert.equal(quote.network, 'eip155:84532');
const catalog = await tool('work_catalog');
const work = await tool('list_tasks');
console.log(JSON.stringify({
  service: service.name, stage: 'public-testnet', network: service.network,
  mcpServer: initialized.serverInfo, availableTools: tools.length,
  outputKinds: catalog.outputKinds, openTasks: work.tasks.length,
  quote, paymentSubmitted: false,
}, null, 2));
