pub fn allowed(path:&str,method:&str)->bool{
 if path.contains(['\\','?','#','%'])||path.contains("..")||path.len()>500{return false;}
 let segments=path.trim_start_matches('/').split('/').collect::<Vec<_>>();
 if segments.get(0)!=Some(&"api")||segments.get(1)!=Some(&"team"){return false;}
 match (method,segments.as_slice()){
  ("GET",["api","team","snapshot"]|["api","team","questions"]|["api","team","artifacts"])=>true,
  ("GET",["api","team","runs",id])=>!id.is_empty(),
  ("POST",["api","team","runs",id,"pause"|"resume"]|["api","team","agents",id,"chat"]|["api","team","questions",id,"answer"]|["api","team","artifacts",id,"apply"])=>!id.is_empty(),
  _=>false
 }
}
#[cfg(test)]mod tests{use super::allowed;#[test]fn scoped(){assert!(allowed("/api/team/runs/r_123/pause","POST"));assert!(allowed("/api/team/questions/q/answer","POST"));for p in ["/api/profiles","/api/team/companion/redeem","/api/team/runs","/api/team/runs/../snapshot","/api/team/snapshot?token=x","/api/team/agents/a/delete"]{assert!(!allowed(p,"POST"));}}}
