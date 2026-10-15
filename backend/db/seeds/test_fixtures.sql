-- Org tree + a few PUBLISHED honors. path = parent path + '.' + id without dashes (as the app/Factory does).
DO $$
DECLARE d uuid := '0d000000-0000-4000-8000-000000000001';
        u uuid := '0d000000-0000-4000-8000-000000000002';
        a uuid := '0d000000-0000-4000-8000-000000000003';
        pf uuid; cat uuid; h uuid;
BEGIN
  INSERT INTO organizations (id, parent_id, type, name, code, status, path, country)
  VALUES (d, NULL, 'division', 'División Interamericana', 'DIA', 'active', text2ltree(replace(d::text,'-','')), 'México')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO organizations (id, parent_id, type, name, code, status, path, country)
  VALUES (u, d, 'union', 'Unión Mexicana del Norte', 'NMUC', 'active',
          text2ltree(replace(d::text,'-','') || '.' || replace(u::text,'-','')), 'México')
  ON CONFLICT (id) DO NOTHING;
  INSERT INTO organizations (id, parent_id, type, name, code, status, path, city, state, country)
  VALUES (a, u, 'association', 'Asociación Norte de Tamaulipas', 'NTAM', 'active',
          text2ltree(replace(d::text,'-','') || '.' || replace(u::text,'-','') || '.' || replace(a::text,'-','')),
          'Reynosa', 'Tamaulipas', 'México')
  ON CONFLICT (id) DO NOTHING;

  SELECT id INTO pf FROM ministries WHERE slug = 'pathfinders';
  SELECT id INTO cat FROM honor_categories WHERE ministry_id = pf AND slug = 'nature';
  IF NOT EXISTS (SELECT 1 FROM honors WHERE code = 'SEED-NA-001') THEN
    INSERT INTO honors (ministry_id, category_id, name, slug, active, code, status, published_at)
    VALUES (pf, cat, 'Aves', 'seed-aves', true, 'SEED-NA-001', 'PUBLISHED', now()) RETURNING id INTO h;
    INSERT INTO honor_requirements (honor_id, position, description) VALUES (h, 1, 'Identificar 20 aves de tu región.');
    INSERT INTO honors (ministry_id, category_id, name, slug, active, code, status, published_at)
    VALUES (pf, cat, 'Árboles', 'seed-arboles', true, 'SEED-NA-002', 'PUBLISHED', now()) RETURNING id INTO h;
    INSERT INTO honor_requirements (honor_id, position, description) VALUES
      (h, 1, 'Explicar la diferencia entre árboles de hoja caduca y perenne.'),
      (h, 2, 'Identificar 15 árboles.');
    INSERT INTO honors (ministry_id, category_id, name, slug, active, code, status, published_at)
    VALUES (pf, cat, 'Estrellas', 'seed-estrellas', true, 'SEED-NA-003', 'PUBLISHED', now()) RETURNING id INTO h;
    INSERT INTO honor_requirements (honor_id, position, description) VALUES
      (h, 1, 'Identificar 10 constelaciones.'), (h, 2, 'Explicar qué es una estrella.'), (h, 3, 'Leer Salmo 19.');
  END IF;
END $$;
