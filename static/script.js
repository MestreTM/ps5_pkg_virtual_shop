document.addEventListener('DOMContentLoaded', () => {
    const tabsContainer = document.getElementById('tabs');
    const contentContainer = document.getElementById('content-area');
    const loading = document.getElementById('loading');
    const blocker = document.getElementById('ps5-blocker');
    const searchBar = document.getElementById('search-bar');
    const searchButton = document.getElementById('search-button');
    const clearButton = document.getElementById('clear-button');
    const shopTitleElement = document.getElementById('shop-title');
    
    const searchPane = document.getElementById('search-pane');
    const searchTitle = document.getElementById('search-title');

    const packModal = document.getElementById('pack-modal');
    const packModalTitle = document.getElementById('pack-modal-title');
    const packModalList = document.getElementById('pack-modal-list');
    const packModalCancel = document.getElementById('pack-modal-cancel');
    const packModalConfirm = document.getElementById('pack-modal-confirm');

    let pageState = {};
    let activeSearchQuery = '';
    let isSearchActive = false;

    async function initializeApp() {
        await loadSettings();
        checkUserAgent();
        setupModalListeners();
        setupSearchListener();
        setupControllerNavigation();
    }

    async function loadSettings() {
        try {
            const response = await fetch('/api/settings');
            if (!response.ok) return;
            const settings = await response.json();
            if (settings.shop_title) {
                shopTitleElement.textContent = settings.shop_title;
                document.title = settings.shop_title;
            }
        } catch (error) {
            console.error('Could not load shop settings:', error);
        }
    }

    async function checkUserAgent() {
        try {
            const response = await fetch('/api/check_agent');
            if (!response.ok) throw new Error('Failed to verify user agent');
            const data = await response.json();
            if (data.is_ps5) {
                initializeScanner();
            } else {
                blocker.classList.remove('hidden');
                document.body.classList.add('no-scroll');
            }
        } catch (error) {
            console.error('Error during agent check:', error);
            blocker.classList.remove('hidden');
            document.body.classList.add('no-scroll');
        }
    }

    async function initializeScanner() {
        loading.classList.remove('hidden');
        tabsContainer.innerHTML = '';
        const categoryPanes = contentContainer.querySelectorAll('.category-pane');
        categoryPanes.forEach(pane => pane.remove());

        try {
            const response = await fetch(`/api/scan`);
            if (!response.ok) {
                const errorData = await response.json();
                throw new Error(errorData.error || `Error ${response.status}`);
            }
            const data = await response.json();
            const categories = data.categories || [];
            
            if (categories.length > 0) {
                renderTabsAndPanes(categories);
                activateTab(categories[0]);
            } else {
                contentContainer.innerHTML = '<p style="text-align: center;">No .pkg files found.</p>';
            }

        } catch (error) {
            console.error('Error while scanning:', error);
            contentContainer.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
        }
    }
    
    function renderTabsAndPanes(categories) {
        categories.forEach(categoryName => {
            pageState[categoryName] = 1;

            const tabButton = document.createElement('button');
            tabButton.textContent = categoryName;
            tabButton.dataset.category = categoryName;
            tabButton.addEventListener('click', () => activateTab(categoryName));
            tabsContainer.appendChild(tabButton);

            const tabPane = document.createElement('div');
            tabPane.id = `pane-${categoryName}`;
            tabPane.className = 'content-pane category-pane';
            tabPane.innerHTML = `
                <div class="gallery"></div>
                <div class="pagination-controls hidden">
                    <button class="btn-prev" disabled>Previous</button>
                    <span class="page-info">Page 1 / 1</span>
                    <button class="btn-next" disabled>Next</button>
                </div>
            `;
            contentContainer.appendChild(tabPane);
        });
    }

    function activateTab(categoryName) {
        isSearchActive = false;
        activeSearchQuery = '';
        searchBar.value = '';
        
        tabsContainer.classList.remove('hidden');
        searchPane.classList.remove('active');
        searchTitle.classList.add('hidden');
        clearButton.classList.add('hidden');

        tabsContainer.querySelectorAll('button').forEach(btn => {
            btn.classList.toggle('active', btn.dataset.category === categoryName);
        });
        contentContainer.querySelectorAll('.category-pane').forEach(pane => {
            pane.classList.toggle('active', pane.id === `pane-${categoryName}`);
        });
        
        fetchAndRenderPage(categoryName, pageState[categoryName]);
    }
    
    async function fetchAndRenderPage(categoryName, page) {
        loading.classList.remove('hidden');
        const pane = document.getElementById(`pane-${categoryName}`);
        if (!pane) return;

        const gallery = pane.querySelector('.gallery');
        gallery.innerHTML = '';

        try {
            const response = await fetch(`/api/items?category=${categoryName}&page=${page}`);
            if (!response.ok) throw new Error(`Error fetching items for ${categoryName}`);
            const data = await response.json();
            
            renderGallery(pane, data.items || []);
            const pageChangeCallback = (newPage) => fetchAndRenderPage(categoryName, newPage);
            updatePagination(pane, data.current_page, data.total_pages, pageChangeCallback);

        } catch (error) {
            console.error('Error in fetchAndRenderPage:', error);
            gallery.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
        }
    }

    async function fetchAndRenderSearchResults(query, page) {
        loading.classList.remove('hidden');
        isSearchActive = true;
        
        contentContainer.querySelectorAll('.category-pane').forEach(p => p.classList.remove('active'));
        tabsContainer.classList.add('hidden');
        searchPane.classList.add('active');
        searchTitle.classList.remove('hidden');
        searchTitle.textContent = `Results for: "${query}"`;
        clearButton.classList.remove('hidden');
        
        const gallery = searchPane.querySelector('.gallery');
        gallery.innerHTML = '';

        try {
            const url = `/api/search?search=${encodeURIComponent(query)}&page=${page}`;
            const response = await fetch(url);
            if (!response.ok) throw new Error('Error performing search');

            const data = await response.json();
            renderGallery(searchPane, data.items || []);
            const pageChangeCallback = (newPage) => fetchAndRenderSearchResults(query, newPage);
            updatePagination(searchPane, data.current_page, data.total_pages, pageChangeCallback);

        } catch (error) {
            console.error('Error in fetchAndRenderSearchResults:', error);
            gallery.innerHTML = `<p style="color: #ff5555; text-align: center;">Error: ${error.message}</p>`;
        } finally {
            loading.classList.add('hidden');
        }
    }


    function renderGallery(pane, items) {
        const gallery = pane.querySelector('.gallery');
        gallery.innerHTML = '';

        if (items.length === 0) {
            gallery.innerHTML = '<p style="text-align: center;">No packages found.</p>';
            return;
        }
        
        items.forEach(pkg => renderPkgCard(pkg, gallery));
    }
    
    function renderPkgCard(pkg, container) {
        const cardButton = document.createElement('button');
        cardButton.className = 'pkg-card'; 
        
        let badgeContainerHTML = '<div class="badge-container">';

        if (pkg.is_pack) {
            badgeContainerHTML += `<span class="pack-badge">PACK</span>`;
            badgeContainerHTML += `<span class="size-badge">${pkg.file_size_str}</span>`;
        } else {
            badgeContainerHTML += `<span class="size-badge">${pkg.file_size_str}</span>`;
        }
        badgeContainerHTML += '</div>';

        
        if (pkg.is_pack) {
            cardButton.setAttribute('onClick', `showPackModal(${JSON.stringify(pkg.title)}, ${JSON.stringify(pkg.items)})`);
            cardButton.innerHTML = `
                ${badgeContainerHTML}
                <div class="img-container">
                    ${pkg.image_path ? 
                        `<img class="btn-img" loading="lazy" src="${pkg.image_path}?t=${new Date().getTime()}" alt="${pkg.title}">` : 
                        `<span class="no-image">No Image</span>`
                    }
                </div>
                <div class="info">
                    <p class="title">${pkg.title}</p>
                    <p class="file-size">(${pkg.items.length} items)</p>
                </div>
            `;
        } else {
            const ip = window.location.hostname;
            const port = window.location.port;
            const pkgUrl = `http://${ip}${port ? ':' + port : ''}${pkg.install_url}`;
            
            cardButton.setAttribute('onClick', `installPkg('${pkgUrl}')`);
            cardButton.innerHTML = `
                ${badgeContainerHTML}
                <div class="img-container">
                    ${pkg.image_path ? 
                        `<img class="btn-img" loading="lazy" src="${pkg.image_path}?t=${new Date().getTime()}" alt="${pkg.title || pkg.content_id}">` : 
                        `<span class="no-image">No Image</span>`
                    }
                </div>
                <div class="info">
                    <p class="title">${pkg.title || pkg.content_id}</p>
                    <p class="file-size">&nbsp;</p>
                </div>
            `;
        }
        container.appendChild(cardButton);
    }


    function updatePagination(pane, currentPage, totalPages, pageChangeCallback) {
        const controls = pane.querySelector('.pagination-controls');
        const prevBtn = pane.querySelector('.btn-prev');
        const nextBtn = pane.querySelector('.btn-next');
        const pageInfo = pane.querySelector('.page-info');

        if (totalPages > 1) {
            pageInfo.textContent = `Page ${currentPage} / ${totalPages}`;
            prevBtn.disabled = (currentPage === 1);
            nextBtn.disabled = (currentPage === totalPages);
            
            prevBtn.onclick = () => pageChangeCallback(currentPage - 1);
            nextBtn.onclick = () => pageChangeCallback(currentPage + 1);

            controls.classList.remove('hidden');
        } else {
            controls.classList.add('hidden');
        }
    }

    function setupSearchListener() {
        const performSearch = () => {
            const searchTerm = searchBar.value.trim();
            if (searchTerm) {
                activeSearchQuery = searchTerm;
                fetchAndRenderSearchResults(searchTerm, 1);
            }
        };

        const clearSearch = () => {
            searchBar.value = '';
            const activeTabButton = tabsContainer.querySelector('button.active') || tabsContainer.querySelector('button');
            if (activeTabButton) {
                activateTab(activeTabButton.dataset.category);
            }
        };

        searchButton.addEventListener('click', performSearch);
        clearButton.addEventListener('click', clearSearch);
        searchBar.addEventListener('keydown', (event) => {
            if (event.key === 'Enter') {
                event.preventDefault();
                performSearch();
            }
        });
    }

    function setupControllerNavigation() {
        document.addEventListener('keydown', (event) => {
            if (searchBar === document.activeElement || isSearchActive) return;

            const tabButtons = Array.from(tabsContainer.querySelectorAll('button'));
            if (tabButtons.length < 2) return;
            const currentIndex = tabButtons.findIndex(btn => btn.classList.contains('active'));
            let nextIndex = -1;

            if (event.keyCode === 118) { // L2
                event.preventDefault();
                nextIndex = (currentIndex - 1 + tabButtons.length) % tabButtons.length;
            } else if (event.keyCode === 119) { // R2
                event.preventDefault();
                nextIndex = (currentIndex + 1) % tabButtons.length;
            }

            if (nextIndex !== -1) {
                tabButtons[nextIndex].click();
            }
        });
    }

    function formatCategoryType(type) {
        const types = {
            'gd': 'Game', 'gde': 'Game',
            'gp': 'Update',
            'ac': 'DLC',
            'ap': 'App',
        };
        return types[type] || type || 'N/A';
    }

    window.showPackModal = (packTitle, items) => {
        packModalTitle.textContent = packTitle;
        packModalList.innerHTML = '';

        if (!items || items.length === 0) {
            packModalList.innerHTML = '<li>This pack is empty.</li>';
        } else {
            items.forEach(item => {
                const li = document.createElement('li');
                const type = formatCategoryType(item.category_type);
                const ip = window.location.hostname;
                const port = window.location.port;
                const pkgUrl = `http://${ip}${port ? ':' + port : ''}${item.install_url}`;

                li.innerHTML = `
                    <span class="pkg-details">
                        ${item.title}
                        <span class="pkg-type">${type}</span>
                    </span>
                    <button class="btn-modal btn-individual-install">Install</button>
                `;
                li.querySelector('.btn-individual-install').onclick = () => installPkg(pkgUrl);
                packModalList.appendChild(li);
            });
        }
        
        packModalConfirm.onclick = () => installAllPkgs(items);
        
        packModal.classList.remove('hidden');
        document.body.classList.add('no-scroll');
    };

    function closePackModal() {
        packModal.classList.add('hidden');
        document.body.classList.remove('no-scroll');
    }

    function setupModalListeners() {
        packModalCancel.addEventListener('click', closePackModal);
        packModal.addEventListener('click', (e) => {
            if (e.target === packModal) {
                closePackModal();
            }
        });
    }

    function installAllPkgs(items) {
        if (!items || items.length === 0) {
            closePackModal();
            return;
        }
        
        closePackModal();
        const originalItems = [...items]; 

        function sendNextPkg(index) {
            if (index >= originalItems.length) {
                showToast(`All ${originalItems.length} downloads started on PS5!`, true);
                return;
            }

            const item = originalItems[index];
            const ip = window.location.hostname;
            const port = window.location.port;
            const pkgUrl = `http://${ip}${port ? ':' + port : ''}${item.install_url}`;

            showToast(`Sending ${index + 1} of ${originalItems.length}: ${item.title}...`, true, 0);

            window.sendPkgToInstaller(pkgUrl)
                .then(() => {
                    setTimeout(() => {
                        sendNextPkg(index + 1);
                    }, 150); 
                })
                .catch(err => {
                    console.error("A pack item failed to send:", err);
                    showToast(warningMessage, false);
                });
        }

        sendNextPkg(0);
    }
    
    initializeApp();
});
