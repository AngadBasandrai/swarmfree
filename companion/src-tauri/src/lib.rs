use std::{sync::{Arc,atomic::{AtomicU64,Ordering}},time::{Duration,SystemTime,UNIX_EPOCH}};
use serde::Deserialize;
use serde_json::{Value,json};
use sha2::{Digest,Sha256};
use tokio::sync::Mutex;
mod paths;

#[derive(Deserialize)]
struct Bundle {format:String,origin:String,invite_id:String,invite:String,certificate:String,expires_at:f64}
#[derive(Clone)]
struct Connection {client:reqwest::Client,origin:String,token:String}
#[derive(Default)]
struct Session {connection:Mutex<Option<Connection>>,epoch:AtomicU64}

fn validate(bundle:&Bundle)->Result<reqwest::Url,String>{
 let origin=reqwest::Url::parse(&bundle.origin).map_err(|_|"Invalid HTTPS coordinator origin")?;
 let now=SystemTime::now().duration_since(UNIX_EPOCH).map_err(|_|"System clock is invalid")?.as_secs_f64();
 if bundle.format!="swarm-companion-v1"||!bundle.expires_at.is_finite()||bundle.expires_at<now||bundle.expires_at>now+900.0{return Err("Pairing invitation expired or is invalid".into());}
 if origin.scheme()!="https"||origin.host_str().is_none()||!origin.username().is_empty()||origin.password().is_some()||origin.path()!="/"||origin.query().is_some()||origin.fragment().is_some(){return Err("Use an HTTPS coordinator origin without credentials or paths".into());}
 if bundle.certificate.len()>16000||bundle.invite.len()>200||bundle.invite.len()<20||bundle.invite_id.len()>100{return Err("Pairing bundle exceeds its limits".into());}
 reqwest::Certificate::from_pem(bundle.certificate.as_bytes()).map_err(|_|"Invalid coordinator trust certificate")?;
 Ok(origin)
}

#[tauri::command]
fn inspect_pair(bundle:Bundle)->Result<Value,String>{validate(&bundle)?;Ok(json!({"origin":bundle.origin,"certificate_sha256":format!("{:x}",Sha256::digest(bundle.certificate.as_bytes())),"expires_at":bundle.expires_at}))}

async fn response(response:reqwest::Response)->Result<Value,String>{
 let status=response.status();let mut response=response;let mut bytes=Vec::new();
 while let Some(chunk)=response.chunk().await.map_err(|_|"Coordinator connection interrupted")?{if bytes.len()+chunk.len()>4000000{return Err("Coordinator response exceeds 4 MB".into());}bytes.extend_from_slice(&chunk);}
 let data:Value=serde_json::from_slice(&bytes).map_err(|_|"Invalid coordinator response")?;
 if !status.is_success(){return Err(data.get("detail").and_then(Value::as_str).unwrap_or("Coordinator rejected this action").into());}Ok(data)
}

#[tauri::command]
async fn pair(bundle:Bundle,state:tauri::State<'_,Arc<Session>>)->Result<Value,String>{
 validate(&bundle)?;
 let epoch=state.epoch.fetch_add(1,Ordering::SeqCst)+1;
 *state.connection.lock().await=None;
 let cert=reqwest::Certificate::from_pem(bundle.certificate.as_bytes()).map_err(|_|"Invalid trust certificate")?;
 let client=reqwest::Client::builder().https_only(true).no_proxy().redirect(reqwest::redirect::Policy::none()).timeout(Duration::from_secs(30)).tls_built_in_root_certs(false).add_root_certificate(cert).build().map_err(|_|"Cannot configure TLS")?;
 let origin=bundle.origin.trim_end_matches('/').to_string();
 let result=response(client.post(format!("{origin}/api/team/companion/redeem")).json(&json!({"invite_id":bundle.invite_id,"invite":bundle.invite})).send().await.map_err(|_|"Cannot reach this trusted HTTPS coordinator")?).await?;
 let token=result.get("token").and_then(Value::as_str).ok_or("Coordinator supplied no scoped credential")?.to_string();
 let mut connection=state.connection.lock().await;
 if state.epoch.load(Ordering::SeqCst)!=epoch{return Err("Pairing was cancelled or replaced".into());}
 *connection=Some(Connection{client,origin,token});Ok(result.get("member").cloned().unwrap_or(Value::Null))
}

#[tauri::command]
async fn disconnect(state:tauri::State<'_,Arc<Session>>)->Result<(),String>{state.epoch.fetch_add(1,Ordering::SeqCst);*state.connection.lock().await=None;Ok(())}

#[tauri::command]
async fn remote_request(path:String,method:String,body:Option<Value>,state:tauri::State<'_,Arc<Session>>)->Result<Value,String>{
 if !paths::allowed(&path,&method)||body.as_ref().map(|b|b.to_string().len()).unwrap_or(0)>64000{return Err("Invalid companion request".into());}
 let connection=state.connection.lock().await.as_ref().cloned().ok_or("Pair this phone first")?;
 let mut request=connection.client.request(method.parse().map_err(|_|"Invalid method")?,format!("{}{}",connection.origin,path)).bearer_auth(&connection.token);
 if let Some(body)=body{request=request.json(&body);}let reply=request.send().await.map_err(|_|"Cannot reach the paired coordinator")?;response(reply).await
}

#[cfg_attr(mobile,tauri::mobile_entry_point)]
pub fn run(){
 let builder=tauri::Builder::default();
 #[cfg(mobile)]
 let builder=builder.plugin(tauri_plugin_barcode_scanner::init());
 builder.manage(Arc::new(Session::default())).invoke_handler(tauri::generate_handler![inspect_pair,pair,disconnect,remote_request]).run(tauri::generate_context!()).expect("Cannot run Swarm Companion");
}
