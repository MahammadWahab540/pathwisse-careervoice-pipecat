const { execSync } = require('child_process');

try {
  const raw = execSync(
    'aws logs get-log-events --log-group-name "/aws/apprunner/careervoice-pipecat/bf5e39b16d4c46af824aed0f2f05373a/application" --log-stream-name "instance/0b8aaf0124b345059aaf9f959134104e" --region ap-south-1 --output json',
    {
      encoding: 'utf-8',
      env: { ...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8' },
      maxBuffer: 10 * 1024 * 1024,
    }
  );

  const parsed = JSON.parse(raw);
  console.log(`Found ${parsed.events?.length || 0} log events in active container instance:`);

  let secretLeaked = false;
  for (const event of parsed.events || []) {
    const msg = (event.message || '').trim();
    if (msg.includes('V8ogCSfhVJu') || msg.includes('Bearer V8og')) {
      secretLeaked = true;
    }
    console.log(`[${new Date(event.timestamp).toISOString()}] ${msg}`);
  }

  console.log('\n--- SECURITY AUDIT REPORT ---');
  console.log(`Secret Token Leakage: ${secretLeaked ? 'DETECTED (FAIL)' : 'NONE DETECTED (PASSED)'}`);
} catch (err) {
  console.error('Error fetching logs:', err.message);
}
