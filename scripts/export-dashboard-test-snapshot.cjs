// Read existing reporting databases only. No marketing API clients or refreshers.
const fs = require('node:fs');
const path = require('node:path');
const crypto = require('node:crypto');
const {createRequire} = require('node:module');
const source = '/Users/yypf/elle-media/dashboard/emw3-dashboard';
const requireDashboard = createRequire(path.join(source, 'package.json'));
requireDashboard('@next/env').loadEnvConfig(source);
const {Client} = requireDashboard('pg');
const root = path.resolve(__dirname, '../data/ai-dashboard-fixtures/2026-09-snapshot-20261026');
const manifestPath = path.join(root, 'manifest.json');
const manifest = JSON.parse(fs.readFileSync(manifestPath, 'utf8'));
const range = ['2026-09-01', '2026-09-30'];
const sources = [
  {name:'google-ads', env:'DATABASE_URL', tables:{
    dim_account:null,dim_campaign:null,dim_ad_group:null,
    fact_account_daily:'date',fact_campaign_daily:'date',fact_ad_group_daily:'date',
    fact_ad_daily:'date',fact_keyword_daily:'date',fact_search_term_daily:'date',
    fact_product_daily:'date',fact_campaign_device_daily:'date',fact_campaign_geo_daily:'date',
  }},
  {name:'edm', env:'KLAVIYO_DATABASE_URL', tables:{
    dim_campaign:null,dim_flow:null,
    fact_metric_daily:'date',fact_campaign_daily:'date',fact_campaign_performance:'send_date',
    fact_flow_daily:'date',fact_subscriber_daily:'date',
  }},
];
async function main(){
  for(const source of sources){
    const client = new Client({connectionString:process.env[source.env],ssl:{rejectUnauthorized:false},connectionTimeoutMillis:12000,statement_timeout:60000});
    const receipt = {status:'pending',tables:{}};
    manifest.databaseExports[source.name] = receipt;
    try{
      await client.connect();
      await client.query('BEGIN TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY');
      receipt.exportedAt = new Date().toISOString();
      for(const [table,dateColumn] of Object.entries(source.tables)){
        // Identifiers are fixed in the allowlist above; dates are bound parameters.
        const query = dateColumn
          ? `SELECT * FROM "${table}" WHERE "${dateColumn}" >= $1::date AND "${dateColumn}" < $2::date + interval '1 day' ORDER BY "${dateColumn}"`
          : `SELECT * FROM "${table}"`;
        const result = await client.query(query,dateColumn?range:[]);
        const relative = `last-month/${source.name}/${table}.json`;
        const destination = path.join(root,relative);
        fs.mkdirSync(path.dirname(destination),{recursive:true});
        const bytes = Buffer.from(JSON.stringify({source:source.name,table,range:dateColumn?manifest.range:null,scope:dateColumn?'last-month':'current-dimension-metadata',exportedAt:receipt.exportedAt,rows:result.rows},null,2));
        fs.writeFileSync(destination,bytes,{mode:0o600});
        receipt.tables[table] = {path:relative,rows:result.rowCount,bytes:bytes.length,sha256:crypto.createHash('sha256').update(bytes).digest('hex')};
        console.log(source.name,table,result.rowCount,'rows');
      }
      await client.query('COMMIT');
      receipt.status = 'complete';
    }catch(error){
      await client.query('ROLLBACK').catch(()=>{});
      receipt.status='failed';receipt.errorCode=error.code||error.name;
      process.exitCode=1;
      console.error(source.name,'export failed:',receipt.errorCode);
    }finally{
      await client.end();
      fs.writeFileSync(manifestPath,JSON.stringify(manifest,null,2));
    }
  }
}
main().catch(error=>{console.error(error.code||error.name);process.exitCode=1;});
