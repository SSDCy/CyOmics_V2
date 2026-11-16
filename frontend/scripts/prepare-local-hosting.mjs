import {access, readFile, writeFile} from 'node:fs/promises';

const actual = new URL('../.openai/hosting.json', import.meta.url);
const example = new URL('../.openai/hosting.example.json', import.meta.url);

async function prepare() {
  try {
    await access(actual);
    console.log('Existing local hosting configuration kept unchanged.');
    return;
  } catch (error) {
    if (error.code !== 'ENOENT') throw error;
  }
  const bytes = await readFile(example);
  const value = JSON.parse(bytes.toString('utf8'));
  if (Object.keys(value).length !== 3 || value.project_id !== 'LOCAL_ONLY_NOT_FOR_DEPLOYMENT'
    || value.d1 !== null || value.r2 !== null) {
    throw Error('The hosting example must remain a local-only, non-deployable placeholder.');
  }
  try {
    await writeFile(actual, bytes, {flag: 'wx'});
    console.log('Created local-only hosting configuration. It is not a deployment identity.');
  } catch (error) {
    if (error.code !== 'EEXIST') throw error;
    console.log('Existing local hosting configuration kept unchanged.');
  }
}

await prepare();
