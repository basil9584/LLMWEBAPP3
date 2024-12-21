import { console } from 'node:console';

export async function getEnvVariables() {
  // Assuming you are running this in a Node.js environment
  const envVariables = Object.entries(process.env).map(([key, value]) => ({
    key,
    value
  }));

  console.log('Fetched environment variables:', envVariables);

  return envVariables;
}
